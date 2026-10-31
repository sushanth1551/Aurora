"""Local API token authentication and request rate limits.

Every route except the health checks requires ``Authorization: Bearer <token>``
matching ``AURORA_API_TOKEN``. This protects a single-user local installation
from other local processes and pages in the browser; it is not a user-account
system, and a public deployment needs a separate security review.

- Unset or too-short token: protected routes answer 503 (the app still starts).
- Missing header: 401. Wrong token: 401, and the failure counts towards a
  per-client lockout (429 with ``Retry-After``) to stop brute-force guessing.
- Write methods are rate limited per client after successful authentication.
The token is never logged or returned.
"""

import hmac
import logging

from fastapi import Request

from app.core.config import get_settings
from app.core.exceptions import (
    AuthenticationException,
    ConfigurationException,
    RateLimitException,
)
from app.core.rate_limit import RateLimits

logger = logging.getLogger(__name__)

MIN_TOKEN_LENGTH = 32
WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def _client_key(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _rate_limits(request: Request) -> RateLimits:
    limits = getattr(request.app.state, "rate_limits", None)
    if limits is None:  # app built without create_app(); keep limits per app
        limits = RateLimits.from_settings(get_settings())
        request.app.state.rate_limits = limits
    return limits


def _bearer_token(header: str | None) -> str | None:
    if not header:
        return None
    scheme, _, value = header.partition(" ")
    if scheme.lower() != "bearer" or not value.strip():
        return None
    return value.strip()


async def require_api_token(request: Request) -> None:
    """FastAPI dependency guarding every protected route."""
    configured = get_settings().aurora_api_token
    if configured is None:
        raise ConfigurationException(
            "API authentication is not configured: set AURORA_API_TOKEN "
            "(run scripts\\init-local-secrets.ps1)."
        )
    expected = configured.get_secret_value()
    if len(expected) < MIN_TOKEN_LENGTH:
        raise ConfigurationException(
            f"AURORA_API_TOKEN is too short; use at least {MIN_TOKEN_LENGTH} "
            "random characters (run scripts\\init-local-secrets.ps1)."
        )
    limits = _rate_limits(request)
    client = _client_key(request)
    if (delay := limits.auth_failures.retry_after(client)) is not None:
        raise RateLimitException(
            "Too many failed authentication attempts; try again later.", delay
        )
    presented = _bearer_token(request.headers.get("Authorization"))
    if presented is None:
        raise AuthenticationException()
    if not hmac.compare_digest(presented.encode(), expected.encode()):
        limits.auth_failures.record(client)
        logger.warning("api_auth_failed client=%s", client)
        raise AuthenticationException("The API token is not valid.")
    if request.method in WRITE_METHODS:
        if (delay := limits.writes.acquire(client)) is not None:
            raise RateLimitException("Too many write requests; slow down.", delay)


async def limit_public_requests(request: Request) -> None:
    """Rate limit for unauthenticated routes that touch dependencies."""
    delay = _rate_limits(request).public.acquire(_client_key(request))
    if delay is not None:
        raise RateLimitException("Too many requests; slow down.", delay)
