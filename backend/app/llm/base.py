"""Provider contract and the logic every provider shares.

Subclasses implement ``_complete`` (one raw request; SDK errors raised as
``ProviderError``). The base class adds bounded retries with backoff and jitter
(``retry_async`` honours ``Retry-After``), JSON extraction, Pydantic validation
and one repair attempt for malformed structured output.
"""

import json
import logging
import re
import time
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

from pydantic import BaseModel, ValidationError

from app.core.config import Settings, get_settings
from app.core.logging import log_event
from app.llm.exceptions import (
    LLMOutputError,
    ProviderAuthenticationError,
    ProviderError,
    ProviderRateLimitError,
    ProviderRequestError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from app.llm.models import FinishStatus, LLMResponse
from app.tools.http import parse_retry_after, retry_async

logger = logging.getLogger(__name__)

_FENCE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.DOTALL | re.IGNORECASE)


def extract_json(text: str) -> dict[str, Any]:
    """Parse a JSON object from model text (tolerates a surrounding code fence)."""
    candidate = text.strip()
    fenced = _FENCE.match(candidate)
    if fenced:
        candidate = fenced.group(1)
    try:
        value = json.loads(candidate)
    except json.JSONDecodeError:
        start, end = candidate.find("{"), candidate.rfind("}")
        if start < 0 or end <= start:
            raise
        value = json.loads(candidate[start : end + 1])
    if not isinstance(value, dict):
        raise ValueError("expected a JSON object")
    return value


def classify_sdk_error(provider: str, exc: Exception) -> ProviderError:
    """Map an SDK exception to the shared taxonomy without leaking its message.

    Works from the exception class name and ``status_code`` so it needs neither
    SDK imported.
    """
    name = type(exc).__name__
    status = getattr(exc, "status_code", None)
    headers = getattr(getattr(exc, "response", None), "headers", None)
    retry_after = None
    if headers is not None:
        try:
            retry_after = parse_retry_after(headers.get("retry-after"))
        except Exception:  # noqa: BLE001 - header access must never mask the error
            retry_after = None
    if "Timeout" in name:
        return ProviderTimeoutError(provider, "request timed out")
    if "Connection" in name:
        return ProviderUnavailableError(provider, "connection failed")
    if status == 429 or "RateLimit" in name:
        return ProviderRateLimitError(
            provider, "rate limited", status_code=429, retry_after=retry_after
        )
    if status in (401, 403) or "Authentication" in name or "PermissionDenied" in name:
        return ProviderAuthenticationError(
            provider, "credentials rejected", status_code=status
        )
    if isinstance(status, int) and status >= 500:
        return ProviderUnavailableError(
            provider, f"server error ({status})", status_code=status
        )
    if isinstance(status, int) and 400 <= status < 500:
        return ProviderRequestError(
            provider, f"request rejected ({status})", status_code=status
        )
    return ProviderUnavailableError(provider, f"unexpected failure ({name})")


class LLMProvider(ABC):
    """Typed LLM interface used by all business services."""

    #: Provider key (``openai`` / ``anthropic`` / ``mock``).
    name: str = "llm"

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        model: str | None = None,
        sleep: Callable[[float], Awaitable[None]] | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._model = model
        self._sleep = sleep

    @property
    def model(self) -> str:
        """The configured model id (never silently changed)."""
        return self._model or ""

    @abstractmethod
    async def _complete(
        self,
        prompt: str,
        *,
        system: str | None,
        json_schema: dict[str, Any] | None,
        schema_name: str,
        max_output_tokens: int,
    ) -> LLMResponse:
        """One raw request. Raise ``ProviderError`` subclasses on failure."""

    async def generate(
        self,
        prompt: str,
        *,
        system: str | None = None,
        max_output_tokens: int | None = None,
    ) -> LLMResponse:
        """Free-text generation with bounded retries."""
        limit = max_output_tokens or self._settings.llm_max_output_tokens
        return await self._with_retries(
            lambda: self._complete(
                prompt,
                system=system,
                json_schema=None,
                schema_name="text",
                max_output_tokens=limit,
            )
        )

    async def generate_structured[M: BaseModel](
        self,
        prompt: str,
        schema: type[M],
        *,
        system: str | None = None,
        max_output_tokens: int | None = None,
    ) -> tuple[M, LLMResponse]:
        """Generate JSON validated against ``schema``.

        One repair attempt is made when the output is not valid JSON or fails
        validation; usage of both calls is summed. Raises ``LLMOutputError`` when
        the output is still unusable, so unvalidated data is never returned.
        """
        json_schema = schema.model_json_schema()
        limit = max_output_tokens or self._settings.llm_max_output_tokens

        async def attempt(p: str) -> LLMResponse:
            return await self._with_retries(
                lambda: self._complete(
                    p,
                    system=system,
                    json_schema=json_schema,
                    schema_name=schema.__name__,
                    max_output_tokens=limit,
                )
            )

        response: LLMResponse | None = None
        try:
            response = await attempt(prompt)
            return self._validate(response, schema)
        except LLMOutputError as first:
            # Covers both a bad reply and a provider that rejected the generation
            # outright (e.g. Groq HTTP 400 "does not match the expected schema").
            log_event(
                logger,
                "llm_structured_repair",
                level=logging.WARNING,
                provider=self.name,
                schema=schema.__name__,
                reason=first.message[:200],
            )
            failure = first.message
        repair = (
            f"{prompt}\n\nYour previous reply was rejected ({failure}). "
            "Reply again with ONLY one JSON object that satisfies the schema."
        )
        second = await attempt(repair)
        if response is not None:
            second.usage = response.usage.merged(second.usage)
        second.metadata["repaired"] = True
        return self._validate(second, schema)

    def _validate[M: BaseModel](
        self, response: LLMResponse, schema: type[M]
    ) -> tuple[M, LLMResponse]:
        if response.finish_status is FinishStatus.TRUNCATED:
            raise LLMOutputError(self.name, "output truncated at the token limit")
        try:
            data = extract_json(response.text)
        except (ValueError, json.JSONDecodeError) as exc:
            raise LLMOutputError(self.name, "output is not valid JSON") from exc
        try:
            parsed = schema.model_validate(data)
        except ValidationError as exc:
            fields = sorted({".".join(map(str, e["loc"])) for e in exc.errors()})
            raise LLMOutputError(
                self.name, "output failed schema validation: " + ", ".join(fields[:8])
            ) from exc
        response.parsed = parsed.model_dump(mode="json")
        return parsed, response

    async def stream(
        self, prompt: str, *, system: str | None = None
    ) -> AsyncIterator[str]:
        """Streaming is reserved for future use."""
        raise NotImplementedError(f"{self.name}: streaming is not implemented.")
        yield ""  # pragma: no cover - makes this an async generator

    async def _with_retries(
        self, operation: Callable[[], Awaitable[LLMResponse]]
    ) -> LLMResponse:
        kwargs: dict[str, Any] = {}
        if self._sleep is not None:
            kwargs["sleep"] = self._sleep
        started = time.monotonic()
        response = await retry_async(
            operation,
            provider=self.name,
            max_retries=self._settings.llm_max_retries,
            base_delay=self._settings.llm_retry_base_delay_seconds,
            max_delay=self._settings.llm_retry_max_delay_seconds,
            **kwargs,
        )
        response.metadata.setdefault(
            "total_seconds", round(time.monotonic() - started, 3)
        )
        return response
