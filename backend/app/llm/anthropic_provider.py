"""Anthropic Claude provider (Messages API).

Structured output uses a single forced tool call whose ``input_schema`` is the
Pydantic JSON schema; the tool input is then validated by ``LLMProvider``. The
SDK client is created lazily (never at import or construction) with its own
retries disabled, so the shared bounded retry policy is the only one in effect.
Credentials are never logged.
"""

import json
import time
from typing import Any

from app.core.config import Settings
from app.core.exceptions import ConfigurationException
from app.llm.base import LLMProvider, classify_sdk_error
from app.llm.exceptions import ProviderResponseError
from app.llm.models import FinishStatus, LLMResponse, LLMUsage

_TOOL_NAME = "record_result"
_FINISH = {
    "end_turn": FinishStatus.COMPLETED,
    "tool_use": FinishStatus.COMPLETED,
    "stop_sequence": FinishStatus.COMPLETED,
    "max_tokens": FinishStatus.TRUNCATED,
    "refusal": FinishStatus.FILTERED,
}


class AnthropicProvider(LLMProvider):
    """Claude through the official ``anthropic`` SDK."""

    name = "anthropic"

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
            self._model = self._settings.anthropic_model

    def _get_client(self) -> Any:
        if self._client is None:
            key = self._settings.anthropic_api_key
            if key is None or not key.get_secret_value():
                raise ConfigurationException(
                    "Anthropic is not configured; set ANTHROPIC_API_KEY."
                )
            import anthropic  # lazy: only when a request is made

            self._client = anthropic.AsyncAnthropic(
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
                "Anthropic model is not configured; set ANTHROPIC_MODEL."
            )
        client = self._get_client()
        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_output_tokens,
            "messages": [{"role": "user", "content": prompt}],
            "timeout": self._settings.llm_request_timeout_seconds,
        }
        if system:
            kwargs["system"] = system
        if json_schema is not None:
            kwargs["tools"] = [
                {
                    "name": _TOOL_NAME,
                    "description": f"Record the final {schema_name} result.",
                    "input_schema": json_schema,
                }
            ]
            kwargs["tool_choice"] = {"type": "tool", "name": _TOOL_NAME}
        started = time.monotonic()
        try:
            raw = await client.messages.create(**kwargs)
        except Exception as exc:  # noqa: BLE001 - normalized below, message dropped
            raise classify_sdk_error(self.name, exc) from None
        return self._normalize(raw, time.monotonic() - started, json_schema is not None)

    def _normalize(self, raw: Any, duration: float, structured: bool) -> LLMResponse:
        text_parts: list[str] = []
        tool_input: Any = None
        for block in getattr(raw, "content", None) or []:
            kind = getattr(block, "type", None)
            if kind == "text":
                text_parts.append(getattr(block, "text", "") or "")
            elif kind == "tool_use" and getattr(block, "name", None) == _TOOL_NAME:
                tool_input = getattr(block, "input", None)
        if structured and tool_input is not None:
            text = json.dumps(tool_input)
        else:
            text = "".join(text_parts)
        if not text and getattr(raw, "stop_reason", None) not in ("max_tokens",):
            raise ProviderResponseError(self.name, "empty response")
        usage = getattr(raw, "usage", None)
        return LLMResponse(
            text=text,
            provider=self.name,
            model=str(getattr(raw, "model", None) or self.model),
            usage=LLMUsage(
                input_tokens=getattr(usage, "input_tokens", None),
                output_tokens=getattr(usage, "output_tokens", None),
                cached_tokens=getattr(usage, "cache_read_input_tokens", None),
            ),
            duration_seconds=round(duration, 3),
            finish_status=_FINISH.get(
                str(getattr(raw, "stop_reason", None)), FinishStatus.OTHER
            ),
            request_id=getattr(raw, "id", None),
        )
