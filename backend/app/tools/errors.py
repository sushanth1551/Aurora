"""Provider error taxonomy shared by the SerpApi and Firecrawl adapters.

Messages never include request URLs, headers, or response bodies, because those
can contain API keys or untrusted content.
"""

from app.core.exceptions import IntegrationException


class ProviderError(IntegrationException):
    """Base class for failures talking to an external provider."""

    code = "provider_error"
    #: Whether retrying the same request may succeed.
    retryable = False

    def __init__(
        self,
        provider: str,
        message: str,
        *,
        status_code: int | None = None,
        retry_after: float | None = None,
    ) -> None:
        self.provider = provider
        self.provider_status = status_code
        self.retry_after = retry_after
        super().__init__(f"{provider}: {message}")


class ProviderAuthenticationError(ProviderError):
    """Credentials are missing, invalid, or lack access. Never retried."""

    code = "provider_authentication_error"


class ProviderRequestError(ProviderError):
    """The provider rejected the request as invalid. Never retried."""

    code = "provider_request_error"


class ProviderResponseError(ProviderError):
    """The provider returned a malformed or unusable response."""

    code = "provider_response_error"


class ProviderRateLimitError(ProviderError):
    """The provider throttled the request. Retried with backoff."""

    code = "provider_rate_limited"
    retryable = True


class ProviderUnavailableError(ProviderError):
    """Timeout, network failure, or 5xx. Retried with backoff."""

    code = "provider_unavailable"
    retryable = True


class ProviderTimeoutError(ProviderUnavailableError):
    """A request or batch exceeded its time limit (work may have been done)."""

    code = "provider_timeout"


class ProviderCircuitOpenError(ProviderUnavailableError):
    """Calls are paused after repeated failures; not retried until the cooldown ends."""

    code = "provider_circuit_open"
    retryable = False


class ProviderBudgetError(ProviderError):
    """The per-run provider request budget was reached; nothing was sent."""

    code = "provider_budget_exceeded"
