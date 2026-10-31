"""Groq provider over Groq's OpenAI-compatible Chat Completions endpoint.

``POST {GROQ_BASE_URL}/chat/completions`` (default
``https://api.groq.com/openai/v1``) through HTTPX, so the configured base URL is
used exactly as written and no extra SDK is needed. Chat Completions is Groq's
stable API; its Responses API is in beta and is not used.

Structured output
-----------------
* ``GROQ_STRUCTURED_OUTPUT=json_schema`` (default): ``response_format`` of type
  ``json_schema``, which Groq documents for ``openai/gpt-oss-20b`` and
  ``openai/gpt-oss-120b``. Best-effort mode (``strict: false``) is used because
  strict mode requires every property to be required with
  ``additionalProperties: false``, which AURORA's schemas (optional fields,
  defaults) do not satisfy. In best-effort mode Groq can reject a non-matching
  generation with HTTP 400; that is surfaced as ``LLMOutputError`` so the shared
  repair attempt runs. Pydantic validates every reply regardless.
* ``json_object``: JSON mode for models without JSON-schema support; the schema is
  then given in the prompt and the reply is still Pydantic-validated.

Reasoning models are called with ``include_reasoning: false`` so no reasoning
text is returned or stored. The API key is sent only in the ``Authorization``
header and is never logged; error bodies (which can echo generations) are not
propagated.
"""

import json
import time
from typing import Any

import httpx

from app.core.config import Settings
from app.core.exceptions import ConfigurationException
from app.llm.base import LLMProvider
from app.llm.exceptions import (
    LLMOutputError,
    ProviderResponseError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from app.llm.models import FinishStatus, LLMResponse, LLMUsage
from app.tools.http import (
    DEFAULT_MAX_RESPONSE_BYTES,
    build_timeout,
    client_scope,
    raise_for_provider_status,
)

_FINISH = {
    "stop": FinishStatus.COMPLETED,
    "length": FinishStatus.TRUNCATED,
    "content_filter": FinishStatus.FILTERED,
}
_SCHEMA_REJECTED = ("json_validate_failed", "does not match the expected schema")


def _schema_rejection(response: httpx.Response) -> bool:
    """Whether a 400 is Groq refusing a generation that missed the JSON schema."""
    if response.status_code != 400:
        return False
    try:
        error = response.json().get("error", {})
    except (ValueError, AttributeError):
        return False
    text = f"{error.get('code', '')} {error.get('message', '')}".lower()
    return any(marker in text for marker in _SCHEMA_REJECTED)


class GroqProvider(LLMProvider):
    """Groq models (default ``openai/gpt-oss-20b``) via Chat Completions."""

    name = "groq"

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        client: httpx.AsyncClient | None = None,
        model: str | None = None,
        sleep: Any | None = None,
    ) -> None:
        super().__init__(settings, model=model, sleep=sleep)
        self._client = client
        if self._model is None:
            self._model = self._settings.groq_model

    def _api_key(self) -> str:
        key = self._settings.groq_api_key
        if key is None or not key.get_secret_value():
            raise ConfigurationException("Groq is not configured; set GROQ_API_KEY.")
        return key.get_secret_value()

    def _payload(
        self,
        prompt: str,
        system: str | None,
        json_schema: dict[str, Any] | None,
        schema_name: str,
        max_output_tokens: int,
    ) -> dict[str, Any]:
        s = self._settings
        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "max_completion_tokens": max_output_tokens,
        }
        if s.groq_exclude_reasoning:
            payload["include_reasoning"] = False
        if s.groq_reasoning_effort:
            payload["reasoning_effort"] = s.groq_reasoning_effort
        if json_schema is not None:
            if s.groq_structured_output == "json_schema":
                payload["response_format"] = {
                    "type": "json_schema",
                    "json_schema": {
                        "name": schema_name,
                        "schema": json_schema,
                        "strict": False,
                    },
                }
            else:
                payload["response_format"] = {"type": "json_object"}
                prompt = (
                    f"{prompt}\n\nReturn one JSON object that conforms to this JSON "
                    f"schema:\n{json.dumps(json_schema, separators=(',', ':'))}"
                )
        messages.append({"role": "user", "content": prompt})
        return payload

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
                "Groq model is not configured; set GROQ_MODEL."
            )
        key = self._api_key()
        s = self._settings
        url = s.groq_base_url.rstrip("/") + "/chat/completions"
        payload = self._payload(
            prompt, system, json_schema, schema_name, max_output_tokens
        )
        timeout = build_timeout(
            min(10.0, s.llm_request_timeout_seconds), s.llm_request_timeout_seconds
        )
        started = time.monotonic()
        try:
            async with client_scope(self._client, timeout) as client:
                response = await client.post(
                    url,
                    json=payload,
                    headers={"Authorization": f"Bearer {key}"},
                    timeout=timeout,
                )
        except httpx.TimeoutException:
            raise ProviderTimeoutError(self.name, "request timed out") from None
        except httpx.TransportError:
            raise ProviderUnavailableError(self.name, "connection failed") from None
        if json_schema is not None and _schema_rejection(response):
            raise LLMOutputError(self.name, "generation did not match the JSON schema")
        raise_for_provider_status(self.name, response)
        if len(response.content) > DEFAULT_MAX_RESPONSE_BYTES:
            raise ProviderResponseError(self.name, "response too large")
        return self._normalize(response, time.monotonic() - started)

    def _normalize(self, response: httpx.Response, duration: float) -> LLMResponse:
        try:
            data = response.json()
            choice = data["choices"][0]
            message = choice.get("message") or {}
        except (ValueError, KeyError, IndexError, TypeError, AttributeError):
            raise ProviderResponseError(self.name, "malformed response") from None
        # ``message.reasoning`` (if a model sends it anyway) is deliberately ignored.
        text = message.get("content") or ""
        finish = _FINISH.get(str(choice.get("finish_reason")), FinishStatus.OTHER)
        if not text and finish not in (FinishStatus.TRUNCATED, FinishStatus.FILTERED):
            raise ProviderResponseError(self.name, "empty response")
        usage = data.get("usage") or {}
        details = usage.get("prompt_tokens_details") or {}
        return LLMResponse(
            text=text,
            provider=self.name,
            model=str(data.get("model") or self.model),
            usage=LLMUsage(
                input_tokens=usage.get("prompt_tokens"),
                output_tokens=usage.get("completion_tokens"),
                cached_tokens=details.get("cached_tokens"),
            ),
            duration_seconds=round(duration, 3),
            finish_status=finish,
            request_id=data.get("id") or response.headers.get("x-request-id"),
        )
