"""Provider selection and controlled fallback. All provider-choice logic lives here."""

import logging
from collections.abc import Collection
from typing import Any

from pydantic import BaseModel

from app.core.config import Settings, get_settings
from app.core.exceptions import ConfigurationException
from app.core.logging import log_event
from app.llm.anthropic_provider import AnthropicProvider
from app.llm.base import LLMProvider
from app.llm.exceptions import ProviderError
from app.llm.groq_provider import GroqProvider
from app.llm.mock_provider import MockProvider
from app.llm.models import LLMResponse
from app.llm.openai_provider import OpenAIProvider

logger = logging.getLogger(__name__)

PROVIDER_NAMES = ("openai", "anthropic", "groq", "mock")
#: Errors worth trying another provider for. Authentication, invalid requests and
#: malformed output are *not* included unless a caller opts in explicitly.
DEFAULT_FALLBACK_CODES = frozenset(
    {"provider_rate_limited", "provider_unavailable", "provider_timeout"}
)


def create_provider(
    name: str, settings: Settings | None = None, *, model: str | None = None
) -> LLMProvider:
    """Instantiate one provider (no network access happens here)."""
    settings = settings or get_settings()
    if name == "openai":
        return OpenAIProvider(settings, model=model)
    if name == "anthropic":
        return AnthropicProvider(settings, model=model)
    if name == "groq":
        return GroqProvider(settings, model=model)
    if name == "mock":
        return MockProvider(settings)
    raise ConfigurationException(f"Unknown LLM provider '{name}'.")


def provider_is_configured(name: str, settings: Settings) -> bool:
    """Whether credentials and a model exist for ``name`` (mock always is)."""
    if name == "mock":
        return True
    key = getattr(settings, f"{name}_api_key", None)
    model = getattr(settings, f"{name}_model", None)
    return bool(key and key.get_secret_value() and model)


class FallbackProvider(LLMProvider):
    """Primary provider with an optional secondary for transient failures.

    The model is never changed silently: a response always names the provider and
    model that actually answered, plus ``fallback_from`` / ``fallback_reason``.
    Output is validated by the same schema either way.
    """

    def __init__(
        self,
        primary: LLMProvider,
        secondary: LLMProvider,
        *,
        fallback_on: Collection[str] = DEFAULT_FALLBACK_CODES,
        settings: Settings | None = None,
    ) -> None:
        super().__init__(settings, model=primary.model)
        self.primary = primary
        self.secondary = secondary
        self.fallback_on = frozenset(fallback_on)
        self.name = primary.name

    async def _complete(self, *args: Any, **kwargs: Any) -> LLMResponse:
        raise NotImplementedError("FallbackProvider delegates whole requests.")

    async def generate(self, prompt: str, **kwargs: Any) -> LLMResponse:
        return await self._run(lambda p: p.generate(prompt, **kwargs))

    async def generate_structured[M: BaseModel](  # type: ignore[override]
        self, prompt: str, schema: type[M], **kwargs: Any
    ) -> tuple[M, LLMResponse]:
        return await self._run(
            lambda p: p.generate_structured(prompt, schema, **kwargs)
        )

    async def _run(self, call: Any) -> Any:
        try:
            return await call(self.primary)
        except ProviderError as exc:
            if exc.code not in self.fallback_on:
                raise
            log_event(
                logger,
                "llm_provider_fallback",
                level=logging.WARNING,
                from_provider=self.primary.name,
                to_provider=self.secondary.name,
                reason=exc.code,
            )
            result = await call(self.secondary)
            response: LLMResponse = result[1] if isinstance(result, tuple) else result
            response.metadata["fallback_from"] = self.primary.name
            response.metadata["fallback_reason"] = exc.code
            return result


def build_llm_provider(
    settings: Settings | None = None,
    *,
    preferred_provider: str | None = None,
    preferred_model: str | None = None,
    pricing_models: Collection[str] = (),
    allow_override: bool = False,
) -> LLMProvider:
    """Create the provider for a workflow.

    ``preferred_*`` are honoured only when ``allow_override`` is true, the provider
    is configured, and a custom model is the configured one or has a registered
    price (so callers cannot pick arbitrary expensive models). Fallback is wrapped
    around the result only when ``LLM_ENABLE_PROVIDER_FALLBACK`` is on and the
    fallback provider is configured and different.
    """
    settings = settings or get_settings()
    name: str = settings.llm_provider
    model: str | None = None
    if preferred_provider and preferred_provider != name:
        if not allow_override:
            raise ConfigurationException("Provider override is not permitted.")
        name = preferred_provider
    if preferred_provider and preferred_provider not in PROVIDER_NAMES:
        raise ConfigurationException(f"Unknown LLM provider '{preferred_provider}'.")
    if preferred_model:
        configured = getattr(settings, f"{name}_model", None)
        if preferred_model != configured:
            if not allow_override or f"{name}:{preferred_model}" not in pricing_models:
                raise ConfigurationException(
                    "Model override is not permitted: use the configured model or "
                    "one with an entry in LLM_PRICING_JSON."
                )
        model = preferred_model
    if not provider_is_configured(name, settings) and not (model and name != "mock"):
        raise ConfigurationException(
            f"LLM provider '{name}' is not configured; set "
            f"{name.upper()}_API_KEY and {name.upper()}_MODEL (or LLM_PROVIDER=mock)."
        )
    primary = create_provider(name, settings, model=model)
    fallback = settings.llm_fallback_provider
    if (
        settings.llm_enable_provider_fallback
        and fallback != name
        and provider_is_configured(fallback, settings)
    ):
        return FallbackProvider(
            primary, create_provider(fallback, settings), settings=settings
        )
    return primary
