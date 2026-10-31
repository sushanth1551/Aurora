"""URL normalization and SSRF policy.

Every research path that can trigger an outbound fetch must pass its URL
through ``UrlPolicy.validate`` first. Validation is purely defensive:

* only ``http``/``https``, no embedded credentials, bounded length;
* no local/internal hostnames and no non-global IP addresses (IPv4 and IPv6,
  including IPv4-mapped and translation prefixes);
* hostnames are resolved and *every* returned address must be public;
* optional domain allow-list;
* redirects (preflight only) are followed manually and each hop revalidated.

Known limits (see ``docs/security-and-ssrf.md``): validation and the later
fetch resolve DNS separately, so DNS rebinding cannot be fully excluded, and
fetches performed by Firecrawl happen on its infrastructure where we cannot
control redirects or resolution. Use egress filtering in production.
"""

import asyncio
import ipaddress
import socket
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

import httpx

from app.core.exceptions import ResearchException

DEFAULT_MAX_URL_LENGTH = 2048
DEFAULT_ALLOWED_PORTS = frozenset({80, 443, 8080, 8443})
_DEFAULT_PORT = {"http": 80, "https": 443}
_BLOCKED_SUFFIXES = (
    ".localhost",
    ".local",
    ".localdomain",
    ".internal",
    ".intranet",
    ".lan",
    ".home",
    ".corp",
    ".private",
)
_BLOCKED_V6_NETWORKS = tuple(
    ipaddress.ip_network(net)
    for net in ("64:ff9b::/96", "64:ff9b:1::/48", "2002::/16", "2001::/32")
)

Resolver = Callable[[str], Awaitable[list[str]]]


class UrlRejected(ResearchException):
    """A URL failed policy. ``reason`` is a stable machine-readable code."""

    code = "url_rejected"

    def __init__(self, reason: str, message: str) -> None:
        self.reason = reason
        super().__init__(message)


@dataclass(frozen=True)
class ValidatedUrl:
    """A URL that passed policy at validation time."""

    original: str
    normalized: str
    host: str
    resolved_ips: tuple[str, ...] = ()


@dataclass(frozen=True)
class RedirectTrace:
    """Result of following redirects with revalidation at every hop."""

    final_url: str
    hops: tuple[str, ...]


def is_public_ip(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """True only for globally routable unicast addresses."""
    if isinstance(address, ipaddress.IPv6Address):
        embedded = address.ipv4_mapped
        if embedded is not None:
            return is_public_ip(embedded)
        if any(address in net for net in _BLOCKED_V6_NETWORKS):
            return False
    return (
        address.is_global
        and not address.is_multicast
        and not address.is_private
        and not address.is_loopback
        and not address.is_link_local
        and not address.is_reserved
        and not address.is_unspecified
    )


async def system_resolver(host: str) -> list[str]:
    """Resolve ``host`` to IP strings with the event loop's resolver."""
    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    return sorted({str(info[4][0]) for info in infos})


class UrlPolicy:
    """Validate and normalize URLs before any outbound fetch."""

    def __init__(
        self,
        *,
        resolver: Resolver = system_resolver,
        dns_timeout: float = 3.0,
        max_length: int = DEFAULT_MAX_URL_LENGTH,
        allowed_ports: Iterable[int] = DEFAULT_ALLOWED_PORTS,
        max_redirects: int = 5,
    ) -> None:
        self._resolver = resolver
        self._dns_timeout = dns_timeout
        self._max_length = max_length
        self._allowed_ports = frozenset(allowed_ports)
        self._max_redirects = max_redirects

    # -- synchronous checks -------------------------------------------------

    def normalize(
        self, url: str, allowed_domains: Iterable[str] | None = None
    ) -> tuple[str, str]:
        """Return ``(normalized_url, ascii_host)`` or raise ``UrlRejected``.

        Does not touch the network. Lower-cases scheme/host, IDNA-encodes the
        host, drops default ports and fragments, and leaves the query intact.
        """
        raw = url.strip()
        if not raw:
            raise UrlRejected("malformed_url", "URL is empty.")
        if len(raw) > self._max_length:
            raise UrlRejected("url_too_long", "URL exceeds the length limit.")
        if any(ch.isspace() or ord(ch) < 32 or ord(ch) == 127 for ch in raw):
            raise UrlRejected("malformed_url", "URL contains illegal characters.")
        try:
            parts = urlsplit(raw)
            port = parts.port
        except ValueError as exc:
            raise UrlRejected("malformed_url", "URL is malformed.") from exc

        scheme = parts.scheme.lower()
        if scheme not in _DEFAULT_PORT:
            raise UrlRejected("unsupported_scheme", "Only http(s) URLs are allowed.")
        if "@" in parts.netloc or parts.username is not None:
            raise UrlRejected(
                "credentials_in_url", "URLs with credentials are rejected."
            )
        host = self._normalize_host(parts.hostname)
        self._check_host_name(host)
        if port is not None and port not in self._allowed_ports:
            raise UrlRejected("blocked_port", f"Port {port} is not allowed.")
        self._check_allowed_domain(host, allowed_domains)

        netloc = f"[{host}]" if ":" in host else host
        if port is not None and port != _DEFAULT_PORT[scheme]:
            netloc = f"{netloc}:{port}"
        path = parts.path or "/"
        query = f"?{parts.query}" if parts.query else ""
        return f"{scheme}://{netloc}{path}{query}", host

    @staticmethod
    def _normalize_host(hostname: str | None) -> str:
        if not hostname:
            raise UrlRejected("missing_host", "URL has no host.")
        host = hostname.rstrip(".").lower()
        if not host:
            raise UrlRejected("missing_host", "URL has no host.")
        if host.isascii():
            return host
        try:
            return host.encode("idna").decode("ascii")
        except UnicodeError as exc:
            raise UrlRejected("malformed_url", "Host is not a valid IDN.") from exc

    @staticmethod
    def _check_host_name(host: str) -> None:
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            address = None
        if address is not None:
            if not is_public_ip(address):
                raise UrlRejected("blocked_ip", "Non-public IP addresses are blocked.")
            return
        if "." not in host or host.endswith(_BLOCKED_SUFFIXES) or host == "localhost":
            raise UrlRejected(
                "blocked_hostname", "Local or internal hosts are blocked."
            )

    @staticmethod
    def _check_allowed_domain(host: str, allowed: Iterable[str] | None) -> None:
        if allowed is None:
            return
        domains = [d.lower().rstrip(".") for d in allowed]
        if not any(host == d or host.endswith(f".{d}") for d in domains):
            raise UrlRejected("domain_not_allowed", "Domain is not on the allow-list.")

    # -- network-aware checks -----------------------------------------------

    async def validate(
        self, url: str, allowed_domains: Iterable[str] | None = None
    ) -> ValidatedUrl:
        """Normalize ``url`` and verify all resolved addresses are public."""
        normalized, host = self.normalize(url, allowed_domains)
        try:
            ipaddress.ip_address(host)
        except ValueError:
            resolved = await self._resolve_public(host)
        else:
            resolved = (host,)
        return ValidatedUrl(url, normalized, host, resolved)

    async def _resolve_public(self, host: str) -> tuple[str, ...]:
        try:
            addresses = await asyncio.wait_for(
                self._resolver(host), timeout=self._dns_timeout
            )
        except TimeoutError as exc:
            raise UrlRejected("dns_timeout", "DNS resolution timed out.") from exc
        except OSError as exc:
            raise UrlRejected("dns_failure", "Host could not be resolved.") from exc
        if not addresses:
            raise UrlRejected("dns_failure", "Host could not be resolved.")
        for text in addresses:
            try:
                address = ipaddress.ip_address(text.split("%", 1)[0])
            except ValueError as exc:
                raise UrlRejected("dns_failure", "Unusable DNS answer.") from exc
            if not is_public_ip(address):
                raise UrlRejected(
                    "blocked_ip", "Host resolves to a non-public address."
                )
        return tuple(addresses)

    async def trace_redirects(
        self,
        url: str,
        client: httpx.AsyncClient,
        allowed_domains: Iterable[str] | None = None,
    ) -> RedirectTrace:
        """Follow redirects manually (HEAD), revalidating every hop.

        Automatic redirects are never used. Raises ``UrlRejected`` when a hop
        is unsafe, the chain is too long, or a hop cannot be reached.
        """
        current = (await self.validate(url, allowed_domains)).normalized
        hops = [current]
        for _ in range(self._max_redirects + 1):
            try:
                response = await client.request("HEAD", current, follow_redirects=False)
            except httpx.HTTPError as exc:
                raise UrlRejected(
                    "preflight_failed", f"Preflight failed ({type(exc).__name__})."
                ) from exc
            location = response.headers.get("location")
            if response.status_code not in (301, 302, 303, 307, 308) or not location:
                return RedirectTrace(current, tuple(hops))
            try:
                target = urljoin(current, location)
                current = (await self.validate(target, allowed_domains)).normalized
            except UrlRejected as exc:
                raise UrlRejected(
                    "redirect_blocked", f"Redirect rejected: {exc.message}"
                ) from exc
            hops.append(current)
        raise UrlRejected("too_many_redirects", "Redirect limit exceeded.")
