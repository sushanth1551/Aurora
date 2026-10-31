"""Synthesis, report generation, citation validation and quality gates."""

import json

from app.core.config import Settings
from app.intelligence.models import (
    AnalysisType,
    Contradiction,
    FindingKind,
    InformationGap,
    QualityStatus,
    ReasoningResult,
    ResearchFinding,
    Statement,
)
from app.intelligence.quality_evaluator import QualityEvaluator
from app.intelligence.report_generator import (
    EXPORTERS,
    ReportGenerator,
    md,
    render_markdown,
)
from app.intelligence.synthesis_engine import SynthesisEngine
from app.llm.exceptions import ProviderUnavailableError
from app.llm.mock_provider import MockProvider
from app.llm.usage_tracker import TrackedLLM, UsageTracker
from app.schemas.intelligence import VerificationStatus
from tests.analysis_support import context_of, ev, two_company_evidence

COMPARISON = {
    "positioning": [
        {
            "statement": "Acme targets enterprises",
            "kind": "fact",
            "entity": "Acme",
            "evidence_ids": ["c-a4"],
        },
    ],
    "documented_strengths": [
        {
            "statement": "Beta documents a Zapier integration",
            "kind": "fact",
            "entity": "Beta",
            "evidence_ids": ["c-b3"],
        },
        {
            "statement": "Acme is the best automation vendor",
            "kind": "fact",
            "entity": "Acme",
            "evidence_ids": ["c-a1"],
        },
    ],
    "observed_limitations": [
        {
            "statement": "Beta has no pricing",
            "kind": "fact",
            "entity": "Beta",
            "evidence_ids": [],
        },
    ],
}
MARKET = {
    "trends": [
        {
            "description": "Slack integrations are common",
            "evidence_ids": ["c-a3", "c-b2"],
        },
        {"description": "Single-source trend", "evidence_ids": ["c-a3"]},
    ],
    "opportunities": [
        {
            "description": "Integration marketplaces",
            "evidence_ids": ["c-a3", "c-b3"],
            "rationale": "Both list integrations",
            "analytical_confidence": "high",
        },
        {
            "description": "Expansion into SMEs",
            "evidence_ids": [],
            "rationale": "speculative",
        },
    ],
    "threats": [
        {
            "description": "Price competition",
            "evidence_ids": ["c-a2"],
            "rationale": "Public pricing",
        }
    ],
    "conclusions": [
        {
            "statement": "Acme offers Acme Flow",
            "kind": "fact",
            "evidence_ids": ["c-a1", "c-ghost"],
        }
    ],
}
SUMMARY = {
    "summary": [
        {
            "statement": "Acme offers Acme Flow and integrates with Slack",
            "kind": "fact",
            "evidence_ids": ["c-a1", "c-a3"],
        }
    ]
}


def reasoning() -> ReasoningResult:
    return ReasoningResult(
        findings=[
            ResearchFinding(
                description="Acme offers Acme Flow",
                kind=FindingKind.FACT,
                evidence_ids=["c-a1"],
            )
        ],
        gaps=[
            InformationGap(
                description="No pricing found for Beta.",
                related_entity="Beta",
                predicate="price",
                critical=True,
            )
        ],
        contradictions=[
            Contradiction(
                subject="Beta — headquarters",
                description="Different values",
                evidence_ids=["c-b4", "c-b5"],
            )
        ],
        iterations=1,
        stop_reason="sufficient",
    )


def llm(responses=None, script=None) -> TrackedLLM:
    mock = MockProvider(
        Settings(llm_max_retries=0), responses=responses or {}, script=script
    )
    return TrackedLLM(mock, UsageTracker())


async def synthesize(analysis_type=AnalysisType.COMPETITOR_INTELLIGENCE, provider=None):
    ctx = context_of(two_company_evidence())
    provider = provider or llm(
        {
            "ComparisonOutput": COMPARISON,
            "MarketOutput": MARKET,
            "SummaryOutput": SUMMARY,
        }
    )
    return ctx, await SynthesisEngine(provider).synthesize(
        ctx, reasoning(), analysis_type
    )


async def test_deterministic_comparison_common_features_and_differentiators() -> None:
    _, out = await synthesize()
    comp = out.company_comparison
    assert comp.entities == ["Acme", "Beta"]
    dims = {d.name: d for d in comp.dimensions}
    assert dims["pricing"].by_entity["Acme"][0].evidence_ids == ["c-a2"]
    # the conflicting headquarters claim is not presented as a fact
    assert all(
        "Mumbai" not in s.statement for s in dims["headquarters"].by_entity["Beta"]
    )
    common = [s for s in comp.common_features if "Slack" in s.statement]
    assert common and set(common[0].evidence_ids) == {"c-a3", "c-b2"}
    zapier = [s for s in comp.differentiators if "Zapier" in s.statement][0]
    assert "missing evidence" in zapier.limitations[0]  # absence is not a weakness


async def test_llm_layer_is_validated() -> None:
    _, out = await synthesize()
    ca = out.competitor_analysis
    assert ca.positioning[0].kind is FindingKind.FACT
    ranked = [s for s in ca.documented_strengths if "best" in s.statement][0]
    assert ranked.kind is FindingKind.HYPOTHESIS  # no unsupported rankings
    assert ca.observed_limitations[0].kind is FindingKind.HYPOTHESIS
    assert [t.description for t in out.trends] == ["Slack integrations are common"]
    assert out.trends[0].source_count == 2 and any(
        "discarded" in x for x in out.limitations
    )
    opp, speculative = out.opportunities
    assert not opp.is_hypothesis and opp.confidence.analytical.value in (
        "low",
        "medium",
    )  # capped by rubric
    assert speculative.is_hypothesis and speculative.evidence_ids == []
    assert out.conclusions[0].evidence_ids == ["c-a1"]  # unknown id removed
    assert out.executive_summary[0].evidence_ids == ["c-a1", "c-a3"]


async def test_synthesis_degrades_without_llm_and_never_invents() -> None:
    down = ProviderUnavailableError("mock", "down")
    ctx, out = await synthesize(provider=llm(script=[down, down, down]))
    assert out.llm_degraded and out.competitor_analysis is None and not out.trends
    assert out.executive_summary and all(
        s.kind is FindingKind.FACT for s in out.executive_summary
    )
    assert {i for s in out.executive_summary for i in s.evidence_ids} <= set(
        ctx.evidence_by_id()
    )
    none = await SynthesisEngine(None).synthesize(
        ctx, reasoning(), AnalysisType.MARKET_RESEARCH
    )
    assert none.llm_degraded and none.company_comparison.dimensions


async def build_report(
    analysis_type=AnalysisType.COMPETITOR_INTELLIGENCE, provider="anthropic"
):
    ctx, out = await synthesize(analysis_type)
    from uuid import uuid4

    report = ReportGenerator().build(
        analysis_id=uuid4(), analysis_type=analysis_type, context=ctx, reasoning=reasoning(),
        synthesis=out, provider=provider, model="m",
    )  # fmt: skip
    return ctx, report


async def test_report_contains_required_sections_and_real_citations_only() -> None:
    ctx, report = await build_report()
    assert report.title.startswith("Competitor intelligence report")
    assert report.methodology and report.scope and report.limitations
    assert report.contradictions and report.information_gaps
    assert set(report.comparisons) == {"company", "product"}
    known = ctx.evidence_by_id()
    assert report.sources and all(r.evidence_id in known for r in report.sources)
    assert {r.source_url for r in report.sources} <= {
        e.source_url for e in ctx.evidence
    }
    assert {"c-b4", "c-b5"} <= {r.evidence_id for r in report.sources}
    json.loads(EXPORTERS["json"].export(report))


async def test_markdown_rendering_escapes_untrusted_text() -> None:
    ctx, report = await build_report()
    report.key_findings[0].statement = "<script>alert(1)</script> [click](javascript:x)"
    text = render_markdown(report)
    import re

    # every < and [ ] from source text is backslash-escaped: no HTML, no links
    assert r"\<script\>" in text and r"\[click\](javascript" in text
    assert not re.search(r"(?<!\\)<script", text)
    assert not re.search(r"(?<!\\)\]\(javascript", text)
    assert (
        "## Source references" in text
        and "## Contradictions" in text
        and "c-a1" in text
    )
    assert md("# heading *x*") == "\\# heading \\*x\\*"


async def test_mock_reports_are_flagged() -> None:
    ctx, report = await build_report(provider="mock")
    assert report.metadata["mock_provider"] and "mock" in report.limitations[0]
    quality = QualityEvaluator(Settings()).evaluate_deterministic(report, ctx)
    assert quality.overall_status is not QualityStatus.PASSED
    assert any("mock" in g for g in quality.gate_failures)


async def test_valid_report_passes_gates() -> None:
    ctx, report = await build_report()
    q = QualityEvaluator(Settings()).evaluate_deterministic(report, ctx)
    assert q.invalid_citation_count == 0 and q.structural_validity
    assert q.contradiction_count == 1 and q.undisclosed_contradiction_count == 0
    assert 0 < q.citation_coverage <= 1 and q.source_diversity > 0
    assert q.source_freshness == 1.0
    # hypotheses are not counted as factual statements
    assert q.factual_statements < sum(1 for _ in report.key_findings) + 100


async def test_invalid_and_unsupported_citations_fail_gates() -> None:
    ctx, report = await build_report()
    report.executive_summary = [
        Statement(statement="Acme offers Acme Flow", evidence_ids=["c-missing"]),
        Statement(statement="Acme has 9000 customers", evidence_ids=["c-a1"]),
        Statement(statement="Beta is cheaper", evidence_ids=[]),
        Statement(statement="Totally unrelated weather report", evidence_ids=["c-a2"]),
    ]
    q = QualityEvaluator(Settings()).evaluate_deterministic(report, ctx)
    problems = {i.problem for i in q.issues}
    assert {
        "missing_reference",
        "unsupported_figure",
        "no_citation",
        "weak_support",
    } <= problems
    assert q.invalid_citation_count >= 1 and q.unsupported_claim_count >= 3
    assert q.overall_status is QualityStatus.FAILED


async def test_citation_to_other_job_or_altered_excerpt_is_invalid() -> None:
    ctx, report = await build_report()
    from uuid import uuid4

    other = ev("c-x1", "Acme", "price", "$29 per month", job=uuid4())
    ctx.evidence.append(other)
    ctx.sources[0].evidence_ids.append("c-x1")
    ctx.evidence_by_id()["c-a1"].excerpt = "text that is not on the page"
    report.executive_summary = [
        Statement(statement="Acme price $29 per month", evidence_ids=["c-x1"]),
        Statement(statement="Acme offers product Acme Flow", evidence_ids=["c-a1"]),
    ]
    q = QualityEvaluator(Settings()).evaluate_deterministic(report, ctx)
    problems = {i.problem for i in q.issues}
    assert {
        "wrong_job",
        "excerpt_not_found",
    } <= problems and q.overall_status is QualityStatus.FAILED


async def test_undisclosed_contradictions_and_gaps_require_review() -> None:
    ctx, report = await build_report()
    ctx.conflicts = [
        Contradiction(subject="s", description="d", evidence_ids=["c-a2", "c-a1"])
    ]
    ctx.missing_information = [
        InformationGap(
            description="x",
            related_entity="Acme",
            predicate="headquarters",
            critical=True,
        )
    ]
    q = QualityEvaluator(Settings()).evaluate_deterministic(report, ctx)
    assert q.undisclosed_contradiction_count == 1
    assert any("critical information gap" in g for g in q.gate_failures)
    assert q.overall_status is QualityStatus.REQUIRES_REVIEW


async def test_missing_sections_fail_structure() -> None:
    ctx, report = await build_report(AnalysisType.PRODUCT_COMPARISON)
    report.comparisons = {}
    report.methodology = []
    q = QualityEvaluator(Settings()).evaluate_deterministic(report, ctx)
    assert not q.structural_validity and set(q.missing_sections) == {
        "comparisons",
        "methodology",
    }
    assert q.overall_status is QualityStatus.FAILED and q.completeness_score < 1


async def test_optional_llm_judge_is_supplementary() -> None:
    ctx, report = await build_report()
    judge = llm(
        {
            "JudgeOutput": {
                "issues": [{"location": "key_findings[0]", "problem": "overstated"}],
                "note": "n",
            }
        }
    )
    s = Settings(intelligence_enable_llm_evaluation=True)
    q = await QualityEvaluator(s, judge).evaluate(report, ctx)
    assert q.llm_judgement["issue_count"] == 1
    assert any(i.severity == "warning" and "llm_review" in i.problem for i in q.issues)
    assert q.overall_status is not QualityStatus.PASSED
    off = await QualityEvaluator(Settings(), judge).evaluate(report, ctx)
    assert off.llm_judgement is None


def test_pdf_export_is_not_faked() -> None:
    import pytest

    from app.core.exceptions import ConfigurationException

    with pytest.raises(ConfigurationException):
        EXPORTERS["pdf"].export(None)  # type: ignore[arg-type]
    assert VerificationStatus.SUPPORTED.value == "supported"


async def test_pricing_claims_reach_company_and_product_tables() -> None:
    from app.schemas.intelligence import EntityType

    items = [
        *two_company_evidence(),
        ev(
            "c-p1",
            "Acme",
            "price",
            "Starter: $29 per month",
            entity_type=EntityType.PRICING,
        ),
        ev(
            "c-p2",
            "Acme Flow",
            "price",
            "Pro: $99 per month",
            entity_type=EntityType.PRICING,
        ),
    ]
    ctx = context_of(items)
    out = await SynthesisEngine(None).synthesize(
        ctx, reasoning(), AnalysisType.PRODUCT_COMPARISON
    )
    company = {d.name: d for d in out.company_comparison.dimensions}["pricing"]
    product = {d.name: d for d in out.product_comparison.dimensions}["pricing"]
    assert any(s.evidence_ids == ["c-p1"] for s in company.by_entity["Acme"])
    assert any(s.evidence_ids == ["c-p2"] for s in product.by_entity["Acme Flow"])
