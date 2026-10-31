"""Token usage, cost estimation and budget enforcement.

* Token counts are exactly what the provider reported; missing values stay
  ``None`` and are never guessed.
* Money is an *estimate* computed from a configurable pricing registry
  (``LLM_PRICING_JSON``). No prices are built in: provider prices change, so a
  missing entry yields ``estimated_cost=None`` ("unavailable"), never a guess.
* Budgets (tokens, estimated cost, call count) are checked before every request;
  an exhausted budget raises ``LLMBudgetError`` so callers can degrade.
* Prompts and responses are never stored here.
"""

import json
import logging
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, ValidationError

from app.core.config import Settings
from app.core.exceptions import ConfigurationException
from app.core.logging import log_event
from app.llm.base import LLMProvider
from app.llm.exceptions import LLMBudgetError, ProviderError
from app.llm.models import LLMResponse, LLMUsage
from app.schemas.research import utc_now

logger = logging.getLogger(__name__)

#: Rough prompt-size estimate used only for pre-flight budget checks.
CHARS_PER_TOKEN = 4


def estimate_tokens(text: str) -> int:
    """Cheap token estimate (about four characters per token); never billed."""
    return max(1, len(text) // CHARS_PER_TOKEN)


class ModelPrice(BaseModel):
    """Price per million tokens, in ``currency``. Update from the provider's page."""

    input_per_million: float = Field(ge=0)
    output_per_million: float = Field(ge=0)
    cached_input_per_million: float | None = Field(default=None, ge=0)
    currency: str = "USD"


class PricingRegistry:
    """``"provider:model"`` -> price. Longest matching key prefix wins."""

    def __init__(self, prices: dict[str, ModelPrice] | None = None) -> None:
        self._prices = dict(prices or {})

    @classmethod
    def from_settings(cls, settings: Settings) -> "PricingRegistry":
        raw = settings.llm_pricing_json
        if not raw:
            return cls()
        try:
            data = json.loads(raw)
            return cls({k: ModelPrice.model_validate(v) for k, v in data.items()})
        except (ValueError, ValidationError, AttributeError) as exc:
            raise ConfigurationException(
                "LLM_PRICING_JSON must be a JSON object of "
                '{"provider:model": {"input_per_million": n, "output_per_million": n}}.'
            ) from exc

    def lookup(self, provider: str, model: str) -> ModelPrice | None:
        key = f"{provider}:{model}"
        matches = [k for k in self._prices if key.startswith(k)]
        return self._prices[max(matches, key=len)] if matches else None

    def keys(self) -> list[str]:
        return sorted(self._prices)

    def has_model(self, provider: str, model: str) -> bool:
        return self.lookup(provider, model) is not None

    def estimate(self, provider: str, model: str, usage: LLMUsage) -> float | None:
        """Estimated cost, or ``None`` when pricing or token counts are missing."""
        price = self.lookup(provider, model)
        if price is None or usage.input_tokens is None or usage.output_tokens is None:
            return None
        cached = min(usage.cached_tokens or 0, usage.input_tokens)
        cached_rate = (
            price.cached_input_per_million
            if price.cached_input_per_million is not None
            else price.input_per_million
        )
        fresh = usage.input_tokens - cached
        total = (
            fresh * price.input_per_million
            + cached * cached_rate
            + usage.output_tokens * price.output_per_million
        ) / 1_000_000
        return round(total, 8)


class UsageBudget(BaseModel):
    """Limits for one analysis. ``None`` disables a limit."""

    max_tokens: int | None = None
    max_cost: float | None = None
    max_calls: int | None = None

    @classmethod
    def from_settings(cls, settings: Settings) -> "UsageBudget":
        return cls(
            max_tokens=settings.intelligence_max_token_budget,
            max_cost=settings.intelligence_max_estimated_cost,
            max_calls=settings.intelligence_max_llm_calls,
        )


class UsageRecord(BaseModel):
    """One LLM request. Measured usage and estimated cost are separate fields."""

    id: UUID = Field(default_factory=uuid4)
    research_job_id: UUID | None = None
    analysis_id: UUID | None = None
    provider: str
    model: str
    operation: str
    prompt_version: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cached_tokens: int | None = None
    total_tokens: int | None = None
    duration_seconds: float = 0.0
    estimated_cost: float | None = None
    cost_available: bool = False
    success: bool = True
    error_category: str | None = None
    fallback_from: str | None = None
    fallback_reason: str | None = None
    requested_at: datetime = Field(default_factory=utc_now)


class UsageTracker:
    """Accumulates usage for one analysis and enforces its budget."""

    def __init__(
        self,
        *,
        pricing: PricingRegistry | None = None,
        budget: UsageBudget | None = None,
        research_job_id: UUID | None = None,
        analysis_id: UUID | None = None,
    ) -> None:
        self.pricing = pricing or PricingRegistry()
        self.budget = budget or UsageBudget()
        self.research_job_id = research_job_id
        self.analysis_id = analysis_id
        self.records: list[UsageRecord] = []
        #: How many of ``records`` the caller has already persisted.
        self.persisted = 0

    # -- totals ---------------------------------------------------------------

    @property
    def calls(self) -> int:
        return len(self.records)

    @property
    def tokens_used(self) -> int:
        return sum(r.total_tokens or 0 for r in self.records)

    @property
    def cost_used(self) -> float | None:
        priced = [
            r.estimated_cost for r in self.records if r.estimated_cost is not None
        ]
        return round(sum(priced), 8) if priced else None

    @property
    def cost_complete(self) -> bool:
        """Whether every recorded request had a price (so the total is full)."""
        return all(r.cost_available for r in self.records)

    def snapshot(self) -> dict[str, Any]:
        return {
            "calls": self.calls,
            "failed_calls": sum(1 for r in self.records if not r.success),
            "tokens_used": self.tokens_used,
            "estimated_cost": self.cost_used,
            "cost_complete": self.cost_complete,
            "budget": self.budget.model_dump(),
        }

    # -- budget -----------------------------------------------------------------

    def check(self, provider: str, model: str, prompt_tokens: int = 0) -> None:
        """Raise ``LLMBudgetError`` if the next request would break a limit."""
        b = self.budget
        if b.max_calls is not None and self.calls >= b.max_calls:
            raise LLMBudgetError(provider, "LLM call budget reached")
        if b.max_tokens is not None and self.tokens_used + prompt_tokens > b.max_tokens:
            raise LLMBudgetError(provider, "token budget reached")
        if b.max_cost is not None:
            if not self.pricing.has_model(provider, model):
                raise LLMBudgetError(
                    provider, "cost budget is set but the model has no configured price"
                )
            if (self.cost_used or 0.0) >= b.max_cost:
                raise LLMBudgetError(provider, "estimated cost budget reached")

    @property
    def exhausted(self) -> bool:
        b = self.budget
        return bool(
            (b.max_calls is not None and self.calls >= b.max_calls)
            or (b.max_tokens is not None and self.tokens_used >= b.max_tokens)
            or (
                b.max_cost is not None
                and self.cost_used is not None
                and self.cost_used >= b.max_cost
            )
        )

    # -- recording ----------------------------------------------------------------

    def record_success(
        self,
        response: LLMResponse,
        operation: str,
        prompt_version: str | None = None,
    ) -> UsageRecord:
        cost = self.pricing.estimate(response.provider, response.model, response.usage)
        response.estimated_cost = cost
        record = UsageRecord(
            research_job_id=self.research_job_id,
            analysis_id=self.analysis_id,
            provider=response.provider,
            model=response.model,
            operation=operation,
            prompt_version=prompt_version,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            cached_tokens=response.usage.cached_tokens,
            total_tokens=response.usage.total_tokens,
            duration_seconds=response.duration_seconds,
            estimated_cost=cost,
            cost_available=cost is not None,
            fallback_from=response.metadata.get("fallback_from"),
            fallback_reason=response.metadata.get("fallback_reason"),
        )
        self.records.append(record)
        self._log(record)
        return record

    def record_failure(
        self,
        provider: str,
        model: str,
        operation: str,
        error: Exception,
        prompt_version: str | None = None,
    ) -> UsageRecord:
        record = UsageRecord(
            research_job_id=self.research_job_id,
            analysis_id=self.analysis_id,
            provider=provider,
            model=model,
            operation=operation,
            prompt_version=prompt_version,
            success=False,
            error_category=getattr(error, "code", type(error).__name__),
        )
        self.records.append(record)
        self._log(record)
        return record

    def _log(self, r: UsageRecord) -> None:
        log_event(
            logger,
            "llm_usage",
            analysis_id=r.analysis_id,
            provider=r.provider,
            model=r.model,
            operation=r.operation,
            input_tokens=r.input_tokens,
            output_tokens=r.output_tokens,
            cost=r.estimated_cost,
            success=r.success,
            error=r.error_category,
        )


class TrackedLLM:
    """Budget-checked, usage-recording facade over a provider.

    Business services use this rather than a raw provider so that every request
    is counted. Failures are recorded and re-raised; nothing is fabricated.
    """

    def __init__(self, provider: LLMProvider, tracker: UsageTracker) -> None:
        self.provider = provider
        self.tracker = tracker

    @property
    def provider_name(self) -> str:
        return self.provider.name

    @property
    def model(self) -> str:
        return self.provider.model

    async def structured[M: BaseModel](
        self,
        prompt: str,
        schema: type[M],
        *,
        system: str,
        operation: str,
        prompt_version: str | None = None,
    ) -> M:
        self.tracker.check(
            self.provider.name, self.provider.model, estimate_tokens(prompt + system)
        )
        try:
            parsed, response = await self.provider.generate_structured(
                prompt, schema, system=system
            )
        except ProviderError as exc:
            self.tracker.record_failure(
                self.provider.name, self.provider.model, operation, exc, prompt_version
            )
            raise
        self.tracker.record_success(response, operation, prompt_version)
        return parsed

    async def text(
        self,
        prompt: str,
        *,
        system: str,
        operation: str,
        prompt_version: str | None = None,
    ) -> str:
        self.tracker.check(
            self.provider.name, self.provider.model, estimate_tokens(prompt + system)
        )
        try:
            response = await self.provider.generate(prompt, system=system)
        except ProviderError as exc:
            self.tracker.record_failure(
                self.provider.name, self.provider.model, operation, exc, prompt_version
            )
            raise
        self.tracker.record_success(response, operation, prompt_version)
        return response.text
