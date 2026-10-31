"""Deterministic mock provider: no network, no credentials, no cost.

Structured replies come from ``responses`` (keyed by schema class name) or, when
absent, a minimal schema-valid instance. ``script`` queues one event per request:
an exception instance to raise (rate limit, auth failure, timeout, ...), the
string ``"malformed"`` for unparseable text, or ``None`` for the normal reply.
Token usage is fixed and controllable. Output is *not analysis*; reports built
with this provider are always flagged for review.
"""

import json
from collections import deque
from collections.abc import Awaitable, Callable
from typing import Any

from pydantic import BaseModel

from app.core.config import Settings
from app.llm.base import LLMProvider
from app.llm.models import LLMResponse, LLMUsage

Reply = BaseModel | dict[str, Any] | str | Callable[[str], Any]


def minimal_instance(schema: dict[str, Any], root: dict[str, Any] | None = None) -> Any:
    """Build the smallest value satisfying a Pydantic-generated JSON schema."""
    root = root or schema
    if "$ref" in schema:
        node: Any = root
        for part in schema["$ref"].removeprefix("#/").split("/"):
            node = node[part]
        return minimal_instance(node, root)
    if "default" in schema:
        return schema["default"]
    if "enum" in schema:
        return schema["enum"][0]
    if "const" in schema:
        return schema["const"]
    options = schema.get("anyOf") or schema.get("oneOf")
    if options:
        for option in options:
            if option.get("type") == "null":
                return None
        return minimal_instance(options[0], root)
    kind = schema.get("type")
    if kind == "string":
        return "mock" if schema.get("minLength", 0) else ""
    if kind == "integer":
        return int(
            schema.get(
                "minimum",
                schema.get("exclusiveMinimum", -1) + 1
                if "exclusiveMinimum" in schema
                else 0,
            )
        )
    if kind == "number":
        return float(schema.get("minimum", 0))
    if kind == "boolean":
        return False
    if kind == "array":
        item = schema.get("items", {})
        return [minimal_instance(item, root) for _ in range(schema.get("minItems", 0))]
    if kind == "object" or "properties" in schema:
        required = schema.get("required", [])
        props = schema.get("properties", {})
        return {k: minimal_instance(props[k], root) for k in required if k in props}
    return None


async def _no_sleep(_: float) -> None:
    return None


class MockProvider(LLMProvider):
    """Scriptable deterministic provider."""

    name = "mock"

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        responses: dict[str, Reply] | None = None,
        script: list[Any] | None = None,
        usage: tuple[int, int, int | None] = (100, 50, None),
        model: str = "mock-model",
        text: str = "Mock response.",
        sleep: Callable[[float], Awaitable[None]] | None = None,
    ) -> None:
        super().__init__(settings, model=model, sleep=sleep or _no_sleep)
        self.responses = dict(responses or {})
        self.script: deque[Any] = deque(script or [])
        self.usage = usage
        self.text = text
        self.calls: list[dict[str, Any]] = []

    async def _complete(
        self,
        prompt: str,
        *,
        system: str | None,
        json_schema: dict[str, Any] | None,
        schema_name: str,
        max_output_tokens: int,
    ) -> LLMResponse:
        self.calls.append({"prompt": prompt, "system": system, "schema": schema_name})
        event = self.script.popleft() if self.script else None
        if isinstance(event, Exception):
            raise event
        text = self._text(
            prompt, json_schema, schema_name, malformed=event == "malformed"
        )
        return LLMResponse(
            text=text,
            provider=self.name,
            model=self.model,
            usage=LLMUsage(
                input_tokens=self.usage[0],
                output_tokens=self.usage[1],
                cached_tokens=self.usage[2],
            ),
            duration_seconds=0.001,
            request_id=f"mock-{len(self.calls)}",
        )

    def _text(
        self,
        prompt: str,
        json_schema: dict[str, Any] | None,
        schema_name: str,
        *,
        malformed: bool,
    ) -> str:
        if malformed:
            return "this is {not valid json"
        if json_schema is None:
            return self.text
        reply: Any = self.responses.get(schema_name)
        if callable(reply) and not isinstance(reply, BaseModel):
            reply = reply(prompt)
        if reply is None:
            reply = minimal_instance(json_schema)
        if isinstance(reply, BaseModel):
            reply = reply.model_dump(mode="json")
        return reply if isinstance(reply, str) else json.dumps(reply)
