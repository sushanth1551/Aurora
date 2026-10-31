"""Repeatable Module 4 report-quality evaluation.

Runs a fixed set of research objectives through the full analysis pipeline and
records the *automated* quality checks per objective. It never proves factual
correctness: that column is left for manual review.

Modes
-----
* ``offline`` (default): seeds the Module 3 test fixture job ("Acme") into an
  in-memory SQLite database and uses a deterministic evidence-citing fake
  provider that also plants known faults (an invented evidence id, an
  unsupported figure, a ranking claim). No network, no keys, no cost. This
  checks that the validation layer catches what it must.
* ``live``: uses the configured provider (``LLM_PROVIDER`` from ``.env``) against
  a stored research job in the configured database. It spends LLM credits and
  needs ``AURORA_RUN_LIVE_LLM_TESTS=true`` plus ``--job-id``. Never run it
  against a job you do not own; one analysis is made per objective.

Usage (from the repository root, ``PYTHONPATH=backend``)::

    python scripts/evaluate_module4.py                      # offline, writes docs/module-4-evaluation.md
    python scripts/evaluate_module4.py --out /tmp/eval.md   # offline, custom path
    AURORA_RUN_LIVE_LLM_TESTS=true python scripts/evaluate_module4.py --live --job-id <uuid> --limit 2

API keys are read from settings only and are never printed.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.core.config import Settings, get_settings  # noqa: E402
from app.intelligence.models import AnalysisType, FindingKind  # noqa: E402
from app.schemas.analysis import AnalyzeRequest  # noqa: E402

#: Objectives phrased against what the Acme fixture pages contain (company,
#: product, pricing, about pages). The ``expect`` field lists terms that a
#: correct report should be able to cite; it is used only for the automated
#: relevance check, not as ground truth.
OBJECTIVES: list[dict[str, Any]] = [
    {
        "id": "Q01",
        "objective": "What products does Acme offer and what do they do?",
        "expect": ["Acme Flow", "workflow"],
    },
    {
        "id": "Q02",
        "objective": "What pricing plans does Acme publish, with prices and billing periods?",
        "expect": ["$29", "$99", "month"],
    },
    {"id": "Q03", "objective": "Where is Acme headquartered?", "expect": ["Bengaluru"]},
    {
        "id": "Q04",
        "objective": "When was Acme founded and how large is the company?",
        "expect": ["2016", "250"],
    },
    {
        "id": "Q05",
        "objective": "Which integrations does Acme Flow document?",
        "expect": ["Slack", "Salesforce"],
    },
    {
        "id": "Q06",
        "objective": "Which technologies does Acme mention using?",
        "expect": ["Python", "Kubernetes"],
    },
    {
        "id": "Q07",
        "objective": "Who are Acme's target customers?",
        "expect": ["enterprises", "startups"],
    },
    {
        "id": "Q08",
        "objective": "What features does Acme Flow provide?",
        "expect": ["Visual workflow builder", "Prebuilt connectors"],
    },
    {
        "id": "Q09",
        "objective": "What use cases does Acme describe for its product?",
        "expect": ["Invoice processing"],
    },
    {
        "id": "Q10",
        "objective": "Does Acme offer a free trial, and how is enterprise pricing handled?",
        "expect": ["14-day", "Contact sales"],
    },
    {
        "id": "Q11",
        "objective": "Compare Acme's Starter and Pro plans on price and billing.",
        "expect": ["$29", "$99"],
    },
    {
        "id": "Q12",
        "objective": "Summarise Acme as a company: what it does, where it is based, and its scale.",
        "expect": ["automation", "Bengaluru", "250"],
    },
]
ANALYSIS_TYPE = AnalysisType.COMPANY_INTELLIGENCE
_ID = re.compile(r'id="(c-[0-9a-f]{10})"')
#: Faults the offline fake provider plants on purpose, and what must happen.
PLANTED = {
    "invented_id": "c-0000000000",
    "unsupported_figure": "Acme serves 9000 customers across 40 countries",
    "ranking": "Acme is the best automation vendor in India",
}


def _fault_provider(settings: Settings):
    """Deterministic provider: cites real ids from the prompt, plus planted faults."""
    from app.llm.mock_provider import MockProvider

    def ids(prompt: str, n: int = 3) -> list[str]:
        return _ID.findall(prompt)[:n]

    def analysis(prompt: str) -> dict[str, Any]:
        found = ids(prompt)
        return {
            "findings": [
                {
                    "description": "Evidence-backed statement about Acme",
                    "kind": "fact",
                    "evidence_ids": found[:2],
                },
                {
                    "description": "Observation with an invented citation",
                    "kind": "observation",
                    "evidence_ids": found[:1] + [PLANTED["invented_id"]],
                },
                {
                    "description": PLANTED["unsupported_figure"],
                    "kind": "fact",
                    "evidence_ids": found[:1],
                },
                {
                    "description": PLANTED["ranking"],
                    "kind": "fact",
                    "evidence_ids": found[:1],
                },
            ],
            "gaps": [],
            "contradictions": [],
            "answered_questions": [1],
            "sufficient": True,
            "sufficiency_rationale": "Fixture evidence covers the objective.",
        }

    def summary(prompt: str) -> dict[str, Any]:
        found = ids(prompt, 2)
        return {
            "summary": [
                {
                    "statement": "Acme publishes product and pricing information",
                    "kind": "observation",
                    "evidence_ids": found,
                }
            ]
        }

    def comparison(prompt: str) -> dict[str, Any]:
        found = ids(prompt, 2)
        return {
            "positioning": [
                {
                    "statement": "Acme positions itself for enterprises and startups",
                    "kind": "observation",
                    "evidence_ids": found,
                }
            ],
            "documented_strengths": [
                {
                    "statement": PLANTED["ranking"],
                    "kind": "fact",
                    "evidence_ids": found[:1],
                }
            ],
            "hypotheses": [
                {
                    "statement": "Acme may expand integrations",
                    "kind": "hypothesis",
                    "evidence_ids": [],
                }
            ],
        }

    def market(prompt: str) -> dict[str, Any]:
        found = ids(prompt, 2)
        return {
            "trends": [
                {
                    "description": "Single-source trend that must be discarded",
                    "evidence_ids": found[:1],
                }
            ],
            "opportunities": [
                {
                    "description": "Integration marketplace",
                    "evidence_ids": found,
                    "rationale": "Documented integrations",
                }
            ],
            "threats": [],
            "conclusions": [
                {
                    "statement": "Acme documents its product and pricing publicly",
                    "kind": "observation",
                    "evidence_ids": found,
                }
            ],
        }

    responses: dict[str, Callable[[str], Any] | dict[str, Any]] = {
        "QuestionsOutput": {
            "questions": [{"question": "What does Acme offer?", "priority": 1}]
        },
        "AnalysisOutput": analysis,
        "SummaryOutput": summary,
        "ComparisonOutput": comparison,
        "MarketOutput": market,
    }
    provider = MockProvider(settings, responses=responses)
    provider.name = "mock"  # keep the mock flag so reports stay 'requires_review'
    return provider


async def _evaluate_one(
    service, repo, job_id: UUID, item: dict[str, Any], live: bool
) -> dict[str, Any]:
    response = await service.analyze(
        AnalyzeRequest(
            research_job_id=job_id,
            research_objective=item["objective"],
            analysis_type=ANALYSIS_TYPE,
        )
    )
    report, markdown = await repo.get_report(response.analysis_id)
    quality = (await repo.get_quality(response.analysis_id)).metrics
    usage = await repo.get_usage(response.analysis_id)
    dumped = report.model_dump_json()
    text = f"{markdown}".lower()
    expected_hits = sum(1 for term in item["expect"] if term.lower() in text)
    statements = [*report.executive_summary, *report.key_findings, *report.conclusions]
    if report.competitor_analysis:
        statements += report.competitor_analysis.documented_strengths
    ranking_as_fact = any(
        PLANTED["ranking"].lower() in s.statement.lower() and s.kind is FindingKind.FACT
        for s in statements
    )
    row = {
        "id": item["id"],
        "objective": item["objective"],
        "status": response.status.value,
        "quality": quality.overall_status.value,
        "evidence_items": response.progress.evidence_items,
        "evidence_trimmed": response.progress.evidence_trimmed,
        "citation_coverage": quality.citation_coverage,
        "evidence_coverage": quality.evidence_coverage,
        "invalid_citations": quality.invalid_citation_count,
        "unsupported": quality.unsupported_claim_count,
        "contradictions_disclosed": quality.contradiction_count,
        "undisclosed_contradictions": quality.undisclosed_contradiction_count,
        "relevance": quality.relevance_score,
        "completeness": quality.completeness_score,
        "structural": quality.structural_validity,
        "expected_terms_found": f"{expected_hits}/{len(item['expect'])}",
        "sources": len(report.sources),
        "llm_calls": usage.requests,
        "tokens": usage.total_tokens,
        "estimated_cost": usage.estimated_cost,
        "cost_complete": usage.cost_complete,
        "markdown_chars": len(markdown),
    }
    if not live:
        figure_flagged = any(i.problem == "unsupported_figure" for i in quality.issues)
        row.update(
            invented_id_absent=PLANTED["invented_id"] not in dumped,
            figure_flagged=figure_flagged,
            ranking_downgraded=not ranking_as_fact,
        )
    return row


async def _offline(limit: int | None) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    from sqlalchemy import event
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.pool import StaticPool

    from app.database.models import Base
    from app.database.repositories.analysis_repository import AnalysisRepository
    from tests.analysis_support import analysis_service, seed_acme_job

    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine.sync_engine, "connect")
    def _fk(conn, _):
        conn.execute("PRAGMA foreign_keys=ON")

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    rows: list[dict[str, Any]] = []
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        job_id = await seed_acme_job(session)
        service = analysis_service(
            session, _fault_provider(settings), settings=settings
        )
        repo = AnalysisRepository(session)
        for item in OBJECTIVES[:limit]:
            rows.append(await _evaluate_one(service, repo, job_id, item, live=False))
        contradiction = await _contradiction_check()
    await engine.dispose()
    meta = {
        "mode": "offline",
        "provider": "mock (evidence-citing fake with planted faults)",
        "job": "Acme fixture (tests/intel_support.py)",
        "contradiction_check": contradiction,
    }
    return rows, meta


async def _contradiction_check() -> dict[str, Any]:
    """Synthetic two-company context with a known headquarters conflict."""
    from uuid import uuid4

    from app.intelligence.models import Contradiction, ReasoningResult
    from app.intelligence.quality_evaluator import QualityEvaluator
    from app.intelligence.report_generator import ReportGenerator
    from app.intelligence.synthesis_engine import SynthesisEngine
    from tests.analysis_support import context_of, two_company_evidence

    ctx = context_of(two_company_evidence())
    ctx.conflicts = [
        Contradiction(
            subject="Beta — headquarters",
            description="Pune vs Mumbai",
            evidence_ids=["c-b4", "c-b5"],
        )
    ]
    synthesis = await SynthesisEngine(None).synthesize(
        ctx, ReasoningResult(), AnalysisType.COMPETITOR_INTELLIGENCE
    )
    disclosed = ReasoningResult(contradictions=list(ctx.conflicts))
    evaluator = QualityEvaluator(Settings(_env_file=None))  # type: ignore[call-arg]
    build = ReportGenerator().build
    good = build(
        analysis_id=uuid4(),
        analysis_type=AnalysisType.COMPETITOR_INTELLIGENCE,
        context=ctx,
        reasoning=disclosed,
        synthesis=synthesis,
        provider="anthropic",
        model="m",
    )
    bad = build(
        analysis_id=uuid4(),
        analysis_type=AnalysisType.COMPETITOR_INTELLIGENCE,
        context=ctx,
        reasoning=ReasoningResult(),
        synthesis=synthesis,
        provider="anthropic",
        model="m",
    )
    q_good = evaluator.evaluate_deterministic(good, ctx)
    q_bad = evaluator.evaluate_deterministic(bad, ctx)
    return {
        "conflict_in_context": len(ctx.conflicts),
        "disclosed_report_undisclosed_count": q_good.undisclosed_contradiction_count,
        "disclosed_report_status": q_good.overall_status.value,
        "hidden_report_undisclosed_count": q_bad.undisclosed_contradiction_count,
        "hidden_report_status": q_bad.overall_status.value,
        "conflicting_value_kept_out_of_tables": all(
            "Mumbai" not in s.statement
            for d in (
                synthesis.company_comparison.dimensions
                if synthesis.company_comparison
                else []
            )
            for ss in d.by_entity.values()
            for s in ss
        ),
    }


async def _live(
    job_id: UUID, limit: int | None
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if os.environ.get("AURORA_RUN_LIVE_LLM_TESTS", "false").lower() != "true":
        raise SystemExit(
            "Live mode needs AURORA_RUN_LIVE_LLM_TESTS=true (it spends LLM credits)."
        )
    from app.database.repositories.analysis_repository import AnalysisRepository
    from app.database.repositories.intelligence_repository import IntelligenceRepository
    from app.database.repositories.research_repository import ResearchRepository
    from app.database.session import create_session_factory
    from app.intelligence.context_builder import ContextBuilder
    from app.intelligence.service import IntelligenceAnalysisService

    settings = get_settings()
    if settings.llm_provider == "mock":
        raise SystemExit(
            "LLM_PROVIDER is 'mock'; set LLM_PROVIDER=groq (and GROQ_API_KEY) in .env for a live run."
        )
    rows: list[dict[str, Any]] = []
    async with create_session_factory()() as session:
        service = IntelligenceAnalysisService(
            repository=AnalysisRepository(session),
            builder=ContextBuilder(
                ResearchRepository(session),
                IntelligenceRepository(session),
                settings=settings,
            ),
            executor=None,  # no follow-up research: no SerpApi/Firecrawl calls
            settings=settings,
        )
        repo = AnalysisRepository(session)
        for item in OBJECTIVES[:limit]:
            rows.append(await _evaluate_one(service, repo, job_id, item, live=True))
    meta = {
        "mode": "live",
        "provider": settings.llm_provider,
        "model": getattr(settings, f"{settings.llm_provider}_model", None),
        "job": str(job_id),
        "context_budget": settings.context_budget_for(settings.llm_provider),
    }
    return rows, meta


def _markdown(rows: list[dict[str, Any]], meta: dict[str, Any]) -> str:
    when = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    cols = [
        "id",
        "status",
        "quality",
        "evidence_items",
        "evidence_trimmed",
        "citation_coverage",
        "evidence_coverage",
        "invalid_citations",
        "unsupported",
        "contradictions_disclosed",
        "undisclosed_contradictions",
        "relevance",
        "completeness",
        "structural",
        "expected_terms_found",
        "sources",
        "llm_calls",
        "tokens",
        "estimated_cost",
        "markdown_chars",
    ]
    offline = meta["mode"] == "offline"
    if offline:
        cols += ["invented_id_absent", "figure_flagged", "ranking_downgraded"]
    out = [
        "# Module 4 evaluation results",
        "",
        f"Generated {when} by `scripts/evaluate_module4.py` (mode: **{meta['mode']}**).",
        f"Provider: {meta['provider']}"
        + (f", model: {meta.get('model')}" if meta.get("model") else "")
        + f". Research data: {meta['job']}.",
        "",
        "All columns are **automated structural/lexical checks**. None of them measures factual",
        "correctness; citation coverage says every factual statement cites stored evidence, not",
        "that the statement is true. Factual correctness and readability need the manual review",
        "section below.",
        "",
        "## Automated checks per objective",
        "",
        "| " + " | ".join(cols) + " |",
        "| " + " | ".join("---" for _ in cols) + " |",
    ]
    for r in rows:
        out.append("| " + " | ".join(str(r.get(c, "")) for c in cols) + " |")
    out += ["", "Objectives:", ""]
    out += [f"- **{r['id']}** {r['objective']}" for r in rows]
    out += [
        "",
        f"All objectives run as `{ANALYSIS_TYPE.value}`, which includes only the company",
        "comparison table by design; product-level objectives (features, use cases, trial)",
        "therefore score low on `expected_terms_found`. That column is informational and",
        "is not a gate.",
    ]
    if offline:
        c = meta["contradiction_check"]
        out += [
            "",
            "## Planted-fault detection (offline mode only)",
            "",
            "The fake provider deliberately cites an invented evidence id, states an unsupported",
            'figure ("9000 customers across 40 countries") and makes a ranking claim. Expected:',
            "the invented id never reaches the report, the figure is flagged `unsupported_figure`,",
            "and the ranking is downgraded to a hypothesis. See the last three columns above.",
            "",
            "## Contradiction handling (synthetic two-company context)",
            "",
            f"- Conflicts in context: {c['conflict_in_context']}",
            f"- Report that discloses the conflict: undisclosed={c['disclosed_report_undisclosed_count']}, status={c['disclosed_report_status']}",
            f"- Report that hides the conflict: undisclosed={c['hidden_report_undisclosed_count']}, status={c['hidden_report_status']}",
            f"- Conflicting value kept out of comparison tables: {c['conflicting_value_kept_out_of_tables']}",
        ]
    out += [
        "",
        "## Manual review (to be completed by a person)",
        "",
        "| id | factual correctness | numerical accuracy | readability | notes |",
        "| --- | --- | --- | --- | --- |",
    ]
    note = "not performed: mock output is not real analysis" if offline else "pending"
    out += [f"| {r['id']} | {note} | {note} | {note} | |" for r in rows]
    out += [
        "",
        "Automated and manual results are kept separate on purpose: a report can pass every",
        "automated gate and still be wrong, and a report flagged `requires_review` can be right.",
        "",
    ]
    return "\n".join(out)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="use the configured provider and database (spends credits)",
    )
    parser.add_argument(
        "--job-id", type=UUID, help="stored research job to analyse (live mode)"
    )
    parser.add_argument(
        "--limit", type=int, default=None, help="evaluate only the first N objectives"
    )
    parser.add_argument(
        "--out", type=Path, default=ROOT / "docs" / "module-4-evaluation.md"
    )
    parser.add_argument(
        "--json", action="store_true", help="also print the rows as JSON"
    )
    args = parser.parse_args()
    if args.live:
        if args.job_id is None:
            parser.error("--live needs --job-id")
        rows, meta = asyncio.run(_live(args.job_id, args.limit))
    else:
        rows, meta = asyncio.run(_offline(args.limit))
    args.out.write_text(_markdown(rows, meta), encoding="utf-8")
    print(f"{len(rows)} objective(s) evaluated in {meta['mode']} mode -> {args.out}")
    if args.json:
        print(json.dumps(rows, indent=2, default=str))
    failures = [r["id"] for r in rows if r["invalid_citations"] or not r["structural"]]
    if meta["mode"] == "offline":
        failures += [
            r["id"]
            for r in rows
            if not (
                r["invented_id_absent"]
                and r["figure_flagged"]
                and r["ranking_downgraded"]
            )
        ]
    if failures:
        print("Automated checks FAILED for: " + ", ".join(failures))
        return 1
    print("All automated checks passed (this does not establish factual correctness).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
