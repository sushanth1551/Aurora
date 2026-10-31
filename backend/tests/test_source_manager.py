"""Tests for URL normalization, SSRF policy, redirect handling, and source selection."""

import httpx
import pytest

from app.core.hashing import content_hash
from app.research.source_manager import SourceManager
from app.research.url_policy import UrlPolicy, UrlRejected, is_public_ip
from app.schemas.research import ExtractedPage, ExtractionStatus
from tests.fakes import PUBLIC_IP, make_policy, make_resolver, result


async def reason(policy: UrlPolicy, url: str, **kwargs) -> str:
    with pytest.raises(UrlRejected) as excinfo:
        await policy.validate(url, **kwargs)
    return excinfo.value.reason


# -- acceptance and normalization -------------------------------------------


@pytest.mark.parametrize("scheme", ["http", "https"])
async def test_valid_urls(scheme: str) -> None:
    validated = await make_policy().validate(f"{scheme}://Example.COM/a/b?q=1&r=2")

    assert validated.normalized == f"{scheme}://example.com/a/b?q=1&r=2"
    assert validated.host == "example.com" and validated.resolved_ips == (PUBLIC_IP,)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("https://example.com", "https://example.com/"),
        ("https://example.com:443/x", "https://example.com/x"),
        ("http://example.com:80/x", "http://example.com/x"),
        ("https://example.com:8443/x", "https://example.com:8443/x"),
        ("https://example.com/page#section", "https://example.com/page"),
        ("https://example.com/p?b=2&a=1#frag", "https://example.com/p?b=2&a=1"),
        ("https://EXAMPLE.com./x", "https://example.com/x"),
        ("  https://example.com/x  ", "https://example.com/x"),
        ("https://bücher.example/x", "https://xn--bcher-kva.example/x"),
    ],
)
def test_normalization(raw: str, expected: str) -> None:
    normalized, _ = make_policy().normalize(raw)
    assert normalized == expected


def test_fragment_variants_normalize_identically() -> None:
    policy = make_policy()
    assert (
        policy.normalize("https://a.com/x#one")[0]
        == policy.normalize("https://a.com/x#two")[0]
    )


# -- rejection ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("ftp://example.com/file", "unsupported_scheme"),
        ("file:///etc/passwd", "unsupported_scheme"),
        ("data:text/html,hi", "unsupported_scheme"),
        ("javascript:alert(1)", "unsupported_scheme"),
        ("not a url", "malformed_url"),
        ("", "malformed_url"),
        ("http://", "missing_host"),
        ("http://[::1", "malformed_url"),
        ("https://user:pass@example.com/", "credentials_in_url"),
        ("https://user@example.com/", "credentials_in_url"),
        ("http://localhost/", "blocked_hostname"),
        ("http://app.localhost/", "blocked_hostname"),
        ("http://printer.local/", "blocked_hostname"),
        ("http://metadata.google.internal/", "blocked_hostname"),
        ("http://intranet/", "blocked_hostname"),
        ("http://2130706433/", "blocked_hostname"),
        ("http://10.0.0.5/", "blocked_ip"),
        ("http://192.168.1.1/", "blocked_ip"),
        ("http://172.16.0.1/", "blocked_ip"),
        ("http://127.0.0.1/", "blocked_ip"),
        ("http://169.254.169.254/latest/meta-data", "blocked_ip"),
        ("http://100.64.0.1/", "blocked_ip"),
        ("http://0.0.0.0/", "blocked_ip"),
        ("http://224.0.0.1/", "blocked_ip"),
        ("http://240.0.0.1/", "blocked_ip"),
        ("http://[::1]/", "blocked_ip"),
        ("http://[fe80::1]/", "blocked_ip"),
        ("http://[fc00::1]/", "blocked_ip"),
        ("http://[fd12:3456::1]/", "blocked_ip"),
        ("http://[::ffff:127.0.0.1]/", "blocked_ip"),
        ("http://[::ffff:10.0.0.1]/", "blocked_ip"),
        ("http://[ff02::1]/", "blocked_ip"),
        ("http://[2002:7f00:1::]/", "blocked_ip"),
        ("https://example.com:22/", "blocked_port"),
        ("https://example.com:99999/", "malformed_url"),
    ],
)
async def test_rejections(url: str, expected: str) -> None:
    assert await reason(make_policy(), url) == expected


async def test_oversized_url_is_rejected() -> None:
    url = "https://example.com/" + "a" * 3000
    assert await reason(make_policy(), url) == "url_too_long"


def test_public_ips_are_accepted() -> None:
    import ipaddress

    assert is_public_ip(ipaddress.ip_address("93.184.216.34"))
    assert is_public_ip(ipaddress.ip_address("2606:2800:220:1:248:1893:25c8:1946"))
    assert not is_public_ip(ipaddress.ip_address("::ffff:192.168.0.1"))


async def test_public_ip_literal_is_allowed_without_dns() -> None:
    policy = UrlPolicy(resolver=make_resolver({"x": OSError}))
    validated = await policy.validate("http://93.184.216.34/")
    assert validated.resolved_ips == ("93.184.216.34",)


# -- DNS-based checks ---------------------------------------------------------


async def test_hostname_resolving_to_private_address_is_rejected() -> None:
    policy = make_policy({"evil.example.com": ["10.0.0.7"]})
    assert await reason(policy, "https://evil.example.com/") == "blocked_ip"


async def test_any_private_answer_among_many_rejects() -> None:
    policy = make_policy({"mixed.example.com": [PUBLIC_IP, "127.0.0.1"]})
    assert await reason(policy, "https://mixed.example.com/") == "blocked_ip"


async def test_private_ipv6_answer_is_rejected() -> None:
    policy = make_policy({"v6.example.com": ["fd00::1"]})
    assert await reason(policy, "https://v6.example.com/") == "blocked_ip"


async def test_obfuscated_numeric_host_is_checked_via_dns() -> None:
    # '0x7f.0.0.1' resolves (inet_aton rules) to loopback on real resolvers.
    policy = make_policy({"0x7f.0.0.1": ["127.0.0.1"]})
    assert await reason(policy, "http://0x7f.0.0.1/") == "blocked_ip"


async def test_dns_failure_is_rejected() -> None:
    policy = make_policy({"gone.example.com": OSError})
    assert await reason(policy, "https://gone.example.com/") == "dns_failure"


async def test_dns_timeout_is_rejected() -> None:
    import asyncio

    async def slow(host: str) -> list[str]:
        await asyncio.sleep(1)
        return [PUBLIC_IP]

    policy = UrlPolicy(resolver=slow, dns_timeout=0.01)
    assert await reason(policy, "https://slow.example.com/") == "dns_timeout"


async def test_empty_dns_answer_is_rejected() -> None:
    policy = make_policy({"empty.example.com": []})
    assert await reason(policy, "https://empty.example.com/") == "dns_failure"


# -- domain allow-list --------------------------------------------------------


async def test_domain_allowlist() -> None:
    policy = make_policy()
    allowed = ["example.com"]

    assert (
        await policy.validate("https://example.com/", allowed)
    ).host == "example.com"
    assert (await policy.validate("https://docs.example.com/", allowed)).host
    assert await reason(
        policy, "https://evilexample.com/", allowed_domains=allowed
    ) == ("domain_not_allowed")
    assert await reason(policy, "https://other.org/", allowed_domains=allowed) == (
        "domain_not_allowed"
    )


# -- redirects ----------------------------------------------------------------


def client_for(routes: dict[str, tuple[int, str | None]]) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        status, location = routes[str(request.url)]
        headers = {"location": location} if location else {}
        return httpx.Response(status, headers=headers)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_safe_redirect_chain_is_followed_and_revalidated() -> None:
    routes = {
        "https://a.example.com/": (301, "https://b.example.com/x"),
        "https://b.example.com/x": (302, "/final"),
        "https://b.example.com/final": (200, None),
    }
    async with client_for(routes) as client:
        trace = await make_policy().trace_redirects("https://a.example.com/", client)

    assert trace.final_url == "https://b.example.com/final" and len(trace.hops) == 3


async def test_redirect_to_private_ip_is_blocked() -> None:
    routes = {"https://a.example.com/": (302, "http://169.254.169.254/latest")}
    async with client_for(routes) as client:
        with pytest.raises(UrlRejected) as excinfo:
            await make_policy().trace_redirects("https://a.example.com/", client)

    assert excinfo.value.reason == "redirect_blocked"


async def test_redirect_to_host_resolving_privately_is_blocked() -> None:
    routes = {"https://a.example.com/": (302, "https://sneaky.example.com/")}
    policy = make_policy({"sneaky.example.com": ["192.168.0.10"]})
    async with client_for(routes) as client:
        with pytest.raises(UrlRejected) as excinfo:
            await policy.trace_redirects("https://a.example.com/", client)

    assert excinfo.value.reason == "redirect_blocked"


async def test_redirect_loop_is_bounded() -> None:
    routes = {"https://a.example.com/": (302, "https://a.example.com/")}
    async with client_for(routes) as client:
        with pytest.raises(UrlRejected) as excinfo:
            await make_policy(max_redirects=3).trace_redirects(
                "https://a.example.com/", client
            )

    assert excinfo.value.reason == "too_many_redirects"


async def test_redirect_preflight_does_not_use_automatic_following() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(302, headers={"location": "http://10.0.0.1/"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(UrlRejected):
            await make_policy().trace_redirects("https://a.example.com/", client)

    assert seen == ["https://a.example.com/"]  # the private hop was never requested


# -- source manager -----------------------------------------------------------


async def test_prepare_deduplicates_and_caps() -> None:
    manager = SourceManager(make_policy())
    results = [
        result(1, "https://example.com/a"),
        result(2, "https://example.com/a#frag"),  # duplicate after normalization
        result(3, "http://127.0.0.1/admin"),  # rejected
        result(4, "https://example.org/b"),
        result(5, "https://example.net/c"),
    ]

    prepared = await manager.prepare(results, max_sources=2)

    assert [c.validated.normalized for c in prepared.selected] == [
        "https://example.com/a",
        "https://example.org/b",
    ]
    assert prepared.duplicate_count == 1 and prepared.over_budget_count == 1
    assert len(prepared.rejected) == 1
    assert prepared.rejected[0].rejection.reason == "blocked_ip"


async def test_rejected_sources_are_recorded_not_discarded() -> None:
    manager = SourceManager(make_policy())
    prepared = await manager.prepare([result(1, "file:///etc/passwd")], max_sources=5)

    source = manager.rejected_source(prepared.rejected[0])

    assert source.extraction_status == ExtractionStatus.REJECTED
    assert source.original_url == "file:///etc/passwd"
    assert source.metadata["rejection_reason"] == "unsupported_scheme"
    assert source.error_message


async def test_prepare_applies_domain_allowlist() -> None:
    manager = SourceManager(make_policy())
    prepared = await manager.prepare(
        [result(1, "https://example.com/a"), result(2, "https://other.org/b")],
        max_sources=5,
        allowed_domains=["example.com"],
    )

    assert len(prepared.selected) == 1
    assert prepared.rejected[0].rejection.reason == "domain_not_allowed"


async def test_final_url_is_verified_or_rejected() -> None:
    manager = SourceManager(make_policy({"internal.example.com": ["10.1.1.1"]}))
    good = ExtractedPage(
        original_url="https://a.example.com/",
        metadata={"provider_final_url": "https://B.example.com/x#f"},
    )
    none = ExtractedPage(original_url="https://a.example.com/")
    bad = ExtractedPage(
        original_url="https://a.example.com/",
        metadata={"provider_final_url": "https://internal.example.com/"},
    )

    assert await manager.verify_final_url(good) == "https://b.example.com/x"
    assert await manager.verify_final_url(none) is None
    with pytest.raises(UrlRejected):
        await manager.verify_final_url(bad)


async def test_build_source_keeps_provenance() -> None:
    manager = SourceManager(make_policy())
    prepared = await manager.prepare(
        [result(3, "https://Example.com/a#x", title="Original")], max_sources=1
    )
    page = ExtractedPage(
        original_url="https://example.com/a",
        title="Page Title",
        markdown="# Body",
        status=ExtractionStatus.SUCCEEDED,
        content_hash=content_hash("# Body"),
    )

    source = manager.build_source(
        prepared.selected[0],
        ExtractionStatus.SUCCEEDED,
        page=page,
        final_url="https://example.com/a",
    )

    assert source.original_url == "https://Example.com/a#x"
    assert source.normalized_url == "https://example.com/a"
    assert (
        source.final_url == "https://example.com/a" and source.domain == "example.com"
    )
    assert source.title == "Page Title" and source.extracted_markdown == "# Body"
    assert source.content_hash == content_hash("# Body")
    assert source.metadata["position"] == 3 and source.extracted_at is not None


def test_content_hash_is_stable_across_line_endings() -> None:
    assert content_hash("a\r\nb\n") == content_hash("a\nb")
    assert content_hash("a") != content_hash("b")
