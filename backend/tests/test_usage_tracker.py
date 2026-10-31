"""Usage tracking, pricing registry and budgets."""

import pytest
from pydantic import BaseModel

from app.core.config import Settings
from app.core.exceptions import ConfigurationException
from app.llm.exceptions import LLMBudgetError, ProviderAuthenticationError
from app.llm.mock_provider import MockProvider
from app.llm.models import LLMResponse, LLMUsage
from app.llm.usage_tracker import (
    ModelPrice,
    PricingRegistry,
    TrackedLLM,
    UsageBudget,
    UsageTracker,
)


class Out(BaseModel):
    value: str = "x"


PRICES = PricingRegistry(
    {
        "mock:mock-model": ModelPrice(
            input_per_million=2.0, output_per_million=8.0, cached_input_per_million=0.5
        ),
        "openai:gpt-": ModelPrice(input_per_million=1.0, output_per_million=1.0),
    }
)


def test_cost_estimate_uses_registry_and_cached_rate() -> None:
    usage = LLMUsage(
        input_tokens=1_000_000, output_tokens=500_000, cached_tokens=200_000
    )
    # 800k * 2 + 200k * 0.5 + 500k * 8, per million
    assert PRICES.estimate("mock", "mock-model", usage) == pytest.approx(5.7)
    assert PRICES.estimate(
        "openai", "gpt-anything", LLMUsage(input_tokens=10, output_tokens=0)
    ) == pytest.approx(1e-5)


def test_cost_unavailable_without_price_or_counts() -> None:
    assert (
        PRICES.estimate(
            "anthropic", "claude-x", LLMUsage(input_tokens=1, output_tokens=1)
        )
        is None
    )
    assert PRICES.estimate("mock", "mock-model", LLMUsage(input_tokens=5)) is None
    assert PricingRegistry().keys() == []


def test_pricing_from_settings_and_validation() -> None:
    registry = PricingRegistry.from_settings(
        Settings(
            llm_pricing_json='{"openai:gpt-z": {"input_per_million": 3, "output_per_million": 4}}'
        )
    )
    assert registry.has_model("openai", "gpt-z") and registry.keys() == ["openai:gpt-z"]
    with pytest.raises(ConfigurationException):
        PricingRegistry.from_settings(Settings(llm_pricing_json="{bad"))


def test_records_measured_usage_and_never_invents_counts() -> None:
    tracker = UsageTracker(pricing=PRICES)
    response = LLMResponse(
        text="", provider="mock", model="mock-model", usage=LLMUsage()
    )
    record = tracker.record_success(response, "op")
    assert record.input_tokens is None and record.total_tokens is None
    assert record.estimated_cost is None and record.cost_available is False
    assert tracker.tokens_used == 0 and tracker.cost_complete is False


def test_budget_limits() -> None:
    tracker = UsageTracker(budget=UsageBudget(max_calls=1, max_tokens=100))
    tracker.check("mock", "m", prompt_tokens=50)
    with pytest.raises(LLMBudgetError, match="token"):
        tracker.check("mock", "m", prompt_tokens=101)
    tracker.record_success(
        LLMResponse(
            text="",
            provider="mock",
            model="m",
            usage=LLMUsage(input_tokens=10, output_tokens=5),
        ),
        "op",
    )
    with pytest.raises(LLMBudgetError, match="call budget"):
        tracker.check("mock", "m")
    assert tracker.exhausted


def test_cost_budget_requires_pricing_and_is_enforced() -> None:
    unpriced = UsageTracker(budget=UsageBudget(max_cost=1.0))
    with pytest.raises(LLMBudgetError, match="no configured price"):
        unpriced.check("anthropic", "claude-x")
    priced = UsageTracker(pricing=PRICES, budget=UsageBudget(max_cost=0.0001))
    priced.record_success(
        LLMResponse(
            text="",
            provider="mock",
            model="mock-model",
            usage=LLMUsage(input_tokens=100, output_tokens=100),
        ),
        "op",
    )
    with pytest.raises(LLMBudgetError, match="cost"):
        priced.check("mock", "mock-model")


async def test_tracked_llm_records_success_and_failure() -> None:
    settings = Settings(llm_max_retries=0)
    tracker = UsageTracker(pricing=PRICES)
    llm = TrackedLLM(
        MockProvider(
            settings, script=[None, ProviderAuthenticationError("mock", "bad")]
        ),
        tracker,
    )
    assert (
        await llm.structured(
            "p", Out, system="s", operation="first", prompt_version="v1"
        )
    ).value == "x"
    with pytest.raises(ProviderAuthenticationError):
        await llm.structured("p", Out, system="s", operation="second")
    ok, failed = tracker.records
    assert ok.success and ok.operation == "first" and ok.prompt_version == "v1"
    assert ok.total_tokens == 150 and ok.estimated_cost == pytest.approx(
        (100 * 2 + 50 * 8) / 1e6
    )
    assert (
        not failed.success and failed.error_category == "provider_authentication_error"
    )
    assert tracker.snapshot()["failed_calls"] == 1


async def test_tracked_llm_refuses_when_budget_exhausted() -> None:
    tracker = UsageTracker(budget=UsageBudget(max_calls=0))
    mock = MockProvider(Settings())
    with pytest.raises(LLMBudgetError):
        await TrackedLLM(mock, tracker).text("p", system="s", operation="x")
    assert mock.calls == []  # nothing was sent
