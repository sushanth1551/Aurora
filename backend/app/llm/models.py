"""Normalized LLM response models. No raw SDK objects leave a provider."""

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class FinishStatus(StrEnum):
    """Why generation stopped, normalized across providers."""

    COMPLETED = "completed"
    TRUNCATED = "truncated"  # hit the output-token limit
    FILTERED = "filtered"
    OTHER = "other"


class LLMUsage(BaseModel):
    """Provider-reported token counts. ``None`` means the provider did not say."""

    input_tokens: int | None = None
    output_tokens: int | None = None
    cached_tokens: int | None = None

    @property
    def total_tokens(self) -> int | None:
        if self.input_tokens is None and self.output_tokens is None:
            return None
        return (self.input_tokens or 0) + (self.output_tokens or 0)

    def merged(self, other: "LLMUsage") -> "LLMUsage":
        """Sum two usages (e.g. a call and its structured-output repair)."""

        def add(a: int | None, b: int | None) -> int | None:
            return None if a is None and b is None else (a or 0) + (b or 0)

        return LLMUsage(
            input_tokens=add(self.input_tokens, other.input_tokens),
            output_tokens=add(self.output_tokens, other.output_tokens),
            cached_tokens=add(self.cached_tokens, other.cached_tokens),
        )


class LLMResponse(BaseModel):
    """A completed generation."""

    text: str
    parsed: dict[str, Any] | None = None
    provider: str
    model: str
    usage: LLMUsage = Field(default_factory=LLMUsage)
    duration_seconds: float = 0.0
    finish_status: FinishStatus = FinishStatus.COMPLETED
    request_id: str | None = None
    #: Filled by the usage tracker from the pricing registry; ``None`` = unknown.
    estimated_cost: float | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def input_tokens(self) -> int | None:
        return self.usage.input_tokens

    @property
    def output_tokens(self) -> int | None:
        return self.usage.output_tokens

    @property
    def cached_tokens(self) -> int | None:
        return self.usage.cached_tokens

    @property
    def total_tokens(self) -> int | None:
        return self.usage.total_tokens
