"""Google OAuth 2.0 authorization-code flow with PKCE (read-only scope).

- Scope: ``webmasters.readonly`` only. No write scope is ever requested.
- ``state`` is 32 random bytes, single use, expires (``GSC_OAUTH_STATE_TTL_SECONDS``)
  and is compared in constant time; only its SHA-256 is kept, in memory. The PKCE
  verifier lives in memory only, so a restart simply abandons pending
  authorizations (the user starts again).
- The callback cannot carry the API token (Google redirects the browser), so it is
  the one public route; the state is what authorizes it.
"""

import base64
import hashlib
import hmac
import secrets
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import urlencode

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"  # noqa: S105 - a public endpoint URL
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
SCOPE = "https://www.googleapis.com/auth/webmasters.readonly"
MAX_PENDING = 20


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


@dataclass(frozen=True)
class AuthorizationStart:
    url: str
    expires_in_seconds: int


class OAuthFlows:
    """Pending authorizations (in memory, bounded, expiring)."""

    def __init__(
        self, ttl_seconds: int, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self._ttl = ttl_seconds
        self._clock = clock
        self._pending: OrderedDict[str, tuple[str, float]] = OrderedDict()

    def begin(self, client_id: str, redirect_uri: str) -> AuthorizationStart:
        self._purge()
        state = secrets.token_urlsafe(32)
        verifier = _b64url(secrets.token_bytes(48))
        challenge = _b64url(hashlib.sha256(verifier.encode("ascii")).digest())
        digest = hashlib.sha256(state.encode()).hexdigest()
        self._pending[digest] = (verifier, self._clock() + self._ttl)
        while len(self._pending) > MAX_PENDING:
            self._pending.popitem(last=False)
        query = urlencode(
            {
                "client_id": client_id,
                "redirect_uri": redirect_uri,
                "response_type": "code",
                "scope": SCOPE,
                "access_type": "offline",
                "prompt": "consent",
                "include_granted_scopes": "false",
                "state": state,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
            }
        )
        return AuthorizationStart(f"{AUTH_URL}?{query}", self._ttl)

    def consume(self, state: str) -> str | None:
        """The PKCE verifier for a valid, unexpired, unused ``state``; else ``None``."""
        self._purge()
        digest = hashlib.sha256(state.encode()).hexdigest()
        for stored, (verifier, _) in list(self._pending.items()):
            if hmac.compare_digest(stored, digest):
                del self._pending[stored]  # single use
                return verifier
        return None

    def _purge(self) -> None:
        now = self._clock()
        for key in [k for k, (_, expires) in self._pending.items() if expires <= now]:
            del self._pending[key]
