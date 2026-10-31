"""LLM errors. They extend the Module 2 provider taxonomy so ``retry_async`` and
the API error handling work unchanged. Messages never include prompts, responses
or credentials."""

from app.tools.errors import (
    ProviderAuthenticationError,
    ProviderBudgetError,
    ProviderError,
    ProviderRateLimitError,
    ProviderRequestError,
    ProviderResponseError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)

__all__ = [
    "LLMBudgetError",
    "LLMOutputError",
    "ProviderAuthenticationError",
    "ProviderError",
    "ProviderRateLimitError",
    "ProviderRequestError",
    "ProviderResponseError",
    "ProviderTimeoutError",
    "ProviderUnavailableError",
]


class LLMOutputError(ProviderResponseError):
    """The model returned text that is not valid JSON or fails the schema."""

    code = "llm_invalid_output"


class LLMBudgetError(ProviderBudgetError):
    """A configured token, cost or call budget would be exceeded; nothing was sent."""

    code = "llm_budget_exceeded"
