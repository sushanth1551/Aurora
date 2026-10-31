"""Provider-specific evidence-token budgets: resolution, fallback, rebuilds, metrics."""

import time

from pydantic import SecretStr

from app.core.config import Settings
from app.database.models import IntelligenceAnalysisRecord
from app.database.repositories.analysis_repository import AnalysisRepository
from app.intelligence.models import UnifiedResearchContext
from app.intelligence.reasoning_engine import ReasoningEngine, ReasoningLimits
from app.intelligence.service import effective_context_budget
from app.llm.mock_provider import MockProvider
from app.llm.provider_factory import FallbackProvider, build_llm_provider
from app.llm.usage_tracker import TrackedLLM, UsageTracker
from app.schemas.analysis import AnalyzeRequest
from tests.analysis_support import (
    analysis_service,
    context_of,
    seed_acme_job,
    two_company_evidence,
)


def test_budget_resolution_uses_override_else_global() -> None:
    s = Settings()
    assert s.context_budget_for("groq") == 4000  # shipped default for Groq
    assert s.context_budget_for("openai") == 12_000  # falls back to the global value
    assert s.context_budget_for("anthropic") == 12_000
    assert s.context_budget_for("mock") == 12_000
    custom = Settings(
        intelligence_context_max_tokens=9000,
        intelligence_context_max_tokens_groq=None,
        intelligence_context_max_tokens_mock=2500,
    )
    assert custom.context_budget_for("groq") == 9000  # unset override -> global
    assert custom.context_budget_for("mock") == 2500
    assert custom.context_budget_for("unknown-provider") == 9000


def test_blank_override_means_unset() -> None:
    s = Settings(
        intelligence_context_max_tokens_groq="", intelligence_context_max_tokens=7000
    )
    assert s.intelligence_context_max_tokens_groq is None
    assert s.context_budget_for("groq") == 7000


def test_fallback_uses_the_smaller_budget_of_the_pair() -> None:
    s = Settings(
        llm_provider="groq", groq_api_key=SecretStr("k"), groq_model="m",
        anthropic_api_key=SecretStr("k2"), anthropic_model="c",
        llm_enable_provider_fallback=True, llm_fallback_provider="anthropic",
        intelligence_context_max_tokens_anthropic=3000,
    )  # fmt: skip
    provider = build_llm_provider(s)
    assert isinstance(provider, FallbackProvider)
    budget, source = effective_context_budget(provider, s)
    assert budget == 3000 and source == "anthropic (fallback, smaller)"
    s2 = s.model_copy(update={"intelligence_context_max_tokens_anthropic": 12_000})
    budget, source = effective_context_budget(provider, s2)
    assert budget == 4000 and source == "groq"


async def test_context_rebuild_keeps_the_provider_budget() -> None:
    seen: list[int | None] = []

    class Builder:
        async def build(
            self, job_ids, objective, *, max_sources=None, max_tokens=None, version=1
        ):
            seen.append(max_tokens)
            ctx = context_of(two_company_evidence(), objective)
            ctx.job_ids, ctx.version = job_ids, version
            return ctx

    class Executor:
        async def execute(self, query):
            from uuid import uuid4

            return uuid4()

    calls = {"n": 0}

    def analysis(prompt: str):
        calls["n"] += 1
        return {
            "findings": [],
            "gaps": [
                {
                    "description": "Beta pricing unknown",
                    "related_entity": "Beta",
                    "predicate": "price",
                }
            ],
            "sufficient": calls["n"] > 1,
        }

    mock = MockProvider(
        Settings(),
        responses={
            "AnalysisOutput": analysis,
            "FollowupOutput": {
                "queries": [{"query": "Beta automation pricing", "target_name": "Beta"}]
            },
        },
    )
    engine = ReasoningEngine(TrackedLLM(mock, UsageTracker()), Builder(), Executor())
    limits = ReasoningLimits(
        max_iterations=3, max_followup_per_iteration=2, max_total_queries=4,
        deadline=time.monotonic() + 60, max_tokens=4000, allow_followup=True,
    )  # fmt: skip
    result, ctx = await engine.run(context_of(two_company_evidence()), limits)
    assert result.iterations == 2 and ctx.version == 2
    assert seen == [4000]  # the rebuild used the run's budget, not the global default


async def test_analysis_records_effective_budget_and_trimming(db_session) -> None:
    job = await seed_acme_job(db_session)
    provider = MockProvider(Settings())
    provider.name = "groq"  # budget is chosen by the provider that answers
    settings = Settings(intelligence_context_max_tokens_groq=600)
    service = analysis_service(db_session, provider, settings=settings)
    response = await service.analyze(
        AnalyzeRequest(
            research_job_id=job, research_objective="Analyse Acme products and pricing"
        )
    )
    p = response.progress
    assert p.context_token_budget == 600 and p.context_budget_source == "groq"
    assert 0 < p.context_tokens <= 600
    assert p.evidence_trimmed is True and p.evidence_considered > p.evidence_items
    record = await db_session.get(IntelligenceAnalysisRecord, response.analysis_id)
    assert record.configuration["context_token_budget"] == 600
    assert record.configuration["context_budget_source"] == "groq"
    report, _ = await AnalysisRepository(db_session).get_report(response.analysis_id)
    assert any(
        "Evidence was trimmed to the context budget" in x for x in report.limitations
    )
    # provenance survives trimming: every cited id resolves to a stored URL + excerpt
    assert report.sources and all(r.source_url and r.excerpt for r in report.sources)


async def test_analysis_without_trimming_has_no_trim_note(db_session) -> None:
    job = await seed_acme_job(db_session)
    response = await analysis_service(db_session).analyze(
        AnalyzeRequest(
            research_job_id=job, research_objective="Analyse Acme products and pricing"
        )
    )
    p = response.progress
    assert p.context_token_budget == 12_000 and p.context_budget_source == "mock"
    assert p.evidence_trimmed is False and p.evidence_considered == p.evidence_items
    report, _ = await AnalysisRepository(db_session).get_report(response.analysis_id)
    assert not any("Evidence was trimmed" in x for x in report.limitations)


def test_budget_is_evidence_only_not_prompt_overhead() -> None:
    """The budget bounds the evidence block; instructions are added on top."""
    from app.intelligence.prompts import SYSTEM_PROMPT, TEMPLATES, build_prompt
    from app.intelligence.text import estimate_tokens

    ctx: UnifiedResearchContext = context_of(two_company_evidence())
    ctx.token_estimate = sum(e.token_estimate for e in ctx.evidence)
    prompt = build_prompt(TEMPLATES["evidence_analysis"], ctx.objective, ctx.evidence)
    overhead = estimate_tokens(prompt + SYSTEM_PROMPT) - ctx.token_estimate
    assert overhead > 0  # system prompt + task text are not inside the evidence budget
