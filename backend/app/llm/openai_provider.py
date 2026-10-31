"""OpenAI provider (Responses API).

Uses ``client.responses.create`` with ``text={"format": {"type": "json_schema"}}``
for structured output and ``store=False`` so prompts are not retained by the
provider. The ``openai`` package is imported lazily and the client can be
injected. NOTE: the ``openai`` package is not installed in the development
environment, so these call shapes follow OpenAI's documented Responses API and
are verified only against injected fakes (see ``docs/llm-provider-architecture.md``).
"""

import time
from typing import Any

from app.core.config import Settings
from app.core.exceptions import ConfigurationException
from app.llm.base import LLMProvider, classify_sdk_error
from app.llm.exceptions import ProviderResponseError
from app.llm.models import FinishStatus, LLMResponse, LLMUsage


class OpenAIProvider(LLMProvider):
    """OpenAI models through the official ``openai`` SDK."""

    name = "openai"

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        client: Any | None = None,
        model: str | None = None,
        sleep: Any | None = None,
    ) -> None:
        super().__init__(settings, model=model, sleep=sleep)
        self._client = client
        if self._model is None:
            self._model = self._settings.openai_model

    def _get_client(self) -> Any:
        if self._client is None:
            key = self._settings.openai_api_key
            if key is None or not key.get_secret_value():
                raise ConfigurationException(
                    "OpenAI is not configured; set OPENAI_API_KEY."
                )
            try:
                import openai  # lazy: only when a request is made
            except ImportError as exc:
                raise ConfigurationException(
                    "The 'openai' package is not installed."
                ) from exc
            self._client = openai.AsyncOpenAI(
                api_key=key.get_secret_value(),
                max_retries=0,
                timeout=self._settings.llm_request_timeout_seconds,
            )
        return self._client

    async def _complete(
        self,
        prompt: str,
        *,
        system: str | None,
        json_schema: dict[str, Any] | None,
        schema_name: str,
        max_output_tokens: int,
    ) -> LLMResponse:
        if not self.model:
            raise ConfigurationException(
                "OpenAI model is not configured; set OPENAI_MODEL."
            )
        client = self._get_client()
        kwargs: dict[str, Any] = {
            "model": self.model,
            "input": prompt,
            "max_output_tokens": max_output_tokens,
            "store": False,
            "timeout": self._settings.llm_request_timeout_seconds,
        }
        if system:
            kwargs["instructions"] = system
        if json_schema is not None:
            kwargs["text"] = {
                "format": {
                    "type": "json_schema",
                    "name": schema_name,
                    "schema": json_schema,
                    # Pydantic schemas use defaults/optionals that strict mode
                    # rejects; Pydantic validates the result instead.
                    "strict": False,
                }
            }
        started = time.monotonic()
        try:
            raw = await client.responses.create(**kwargs)
        except Exception as exc:  # noqa: BLE001 - normalized below, message dropped
            raise classify_sdk_error(self.name, exc) from None
        return self._normalize(raw, time.monotonic() - started)

    def _normalize(self, raw: Any, duration: float) -> LLMResponse:
        text = getattr(raw, "output_text", None) or ""
        status = getattr(raw, "status", None)
        reason = getattr(getattr(raw, "incomplete_details", None), "reason", None)
        finish = FinishStatus.COMPLETED
        if status == "incomplete":
            finish = (
                FinishStatus.TRUNCATED
                if reason == "max_output_tokens"
                else FinishStatus.FILTERED
                if reason == "content_filter"
                else FinishStatus.OTHER
            )
        elif status == "failed":
            raise ProviderResponseError(self.name, "response failed")
        if not text and finish is FinishStatus.COMPLETED:
            raise ProviderResponseError(self.name, "empty response")
        usage = getattr(raw, "usage", None)
        details = getattr(usage, "input_tokens_details", None)
        return LLMResponse(
            text=text,
            provider=self.name,
            model=str(getattr(raw, "model", None) or self.model),
            usage=LLMUsage(
                input_tokens=getattr(usage, "input_tokens", None),
                output_tokens=getattr(usage, "output_tokens", None),
                cached_tokens=getattr(details, "cached_tokens", None),
            ),
            duration_seconds=round(duration, 3),
            finish_status=finish,
            request_id=getattr(raw, "id", None),
        )
