"""Report quality evaluation: deterministic citation checks first, optional LLM judge second.

Metric definitions (all heuristic; none measures factual accuracy)
-----------------------------------------------------------------
* factual statement   a report statement of kind ``fact`` or ``observation``
                      (hypotheses and recommendations are listed, not counted).
* valid citation      the evidence ID exists in the context, belongs to one of the
                      analysis's research jobs, has a stored source and (when the
                      source text is stored) its verbatim excerpt occurs in it.
* citation_coverage   factual statements with >=1 valid citation / factual statements.
* evidence_coverage   distinct validly cited evidence / evidence in the context.
* unsupported claim   a factual statement with no citation, only invalid citations,
                      weak lexical support (<25% of its terms found in the cited
                      evidence) or a number absent from the cited evidence.
* invalid_citation    a (statement, evidence ID) pair failing the validity test.
* relevance_score     mean share of statement terms found in the cited evidence.
* completeness_score  required sections present / required sections.
* source_diversity    min(1, distinct cited domains / 3).
* source_freshness    share of cited evidence observed within the freshness window
                      (``None`` when no evidence carries a date).
* structural_validity no required section is missing.

Gates: invalid citations -> failed; missing sections -> failed; unsupported claims
above the limit, coverage below the minimum, undisclosed contradictions or critical
gaps, a degraded LLM or the mock provider -> requires_review; otherwise passed
("passed" means the gates held, not that the content is true).
"""

import logging
from datetime import datetime
from typing import Any

from app.core.config import Settings, get_settings
from app.intelligence.context_builder import normalize_url
from app.intelligence.llm_schemas import JudgeOutput
from app.intelligence.models import (
    AnalysisType,
    CitationIssue,
    EvidenceItem,
    FindingKind,
    IntelligenceReport,
    QualityStatus,
    ReportQualityMetrics,
    UnifiedResearchContext,
)
from app.intelligence.prompts import (
    PROMPT_VERSION,
    SYSTEM_PROMPT,
    TEMPLATES,
    build_prompt,
)
from app.intelligence.text import contains_excerpt, numbers_in, overlap, terms
from app.llm.exceptions import ProviderError
from app.llm.usage_tracker import TrackedLLM
from app.research.domains import registrable_domain
from app.schemas.research import utc_now

logger = logging.getLogger(__name__)

SUPPORT_THRESHOLD = 0.25
_FACTUAL = {FindingKind.FACT, FindingKind.OBSERVATION}
COMPARISON_TYPES = {
    AnalysisType.PRODUCT_COMPARISON,
    AnalysisType.TECHNOLOGY_ANALYSIS,
    AnalysisType.COMPETITOR_INTELLIGENCE,
}


class _Stmt:
    __slots__ = ("location", "text", "kind", "evidence_ids")

    def __init__(self, location: str, text: str, kind: FindingKind, ids: list[str]):
        self.location, self.text, self.kind, self.evidence_ids = (
            location,
            text,
            kind,
            ids,
        )


def collect_statements(report: IntelligenceReport) -> list[_Stmt]:
    """Every statement in the report that makes a claim, with its location."""
    out: list[_Stmt] = []

    def add(location: str, items: list[Any]) -> None:
        for n, s in enumerate(items):
            out.append(
                _Stmt(f"{location}[{n}]", s.statement, s.kind, list(s.evidence_ids))
            )

    add("executive_summary", report.executive_summary)
    add("key_findings", report.key_findings)
    add("company_analysis", report.company_analysis)
    add("product_analysis", report.product_analysis)
    add("technology_analysis", report.technology_analysis)
    add("conclusions", report.conclusions)
    ca = report.competitor_analysis
    if ca:
        for name in (
            "positioning", "documented_strengths", "comparative_advantages",
            "observed_limitations", "hypotheses",
        ):  # fmt: skip
            add(f"competitor_analysis.{name}", getattr(ca, name))
    for key, data in report.comparisons.items():
        for d in data.get("dimensions", []):
            for entity, statements in d.get("by_entity", {}).items():
                for n, s in enumerate(statements):
                    out.append(
                        _Stmt(
                            f"comparisons.{key}.{d['name']}.{entity}[{n}]",
                            s["statement"],
                            FindingKind(s["kind"]),
                            list(s.get("evidence_ids", [])),
                        )
                    )
    strategic: list[tuple[str, list[Any]]] = [
        ("trends", report.trends),
        ("opportunities", report.opportunities),
        ("threats", report.threats),
    ]
    for label, entries in strategic:
        for n, t in enumerate(entries):
            hypothesis = getattr(t, "is_hypothesis", False)
            out.append(
                _Stmt(
                    f"{label}[{n}]",
                    t.description,
                    FindingKind.HYPOTHESIS if hypothesis else FindingKind.OBSERVATION,
                    list(t.evidence_ids),
                )
            )
    return out


class QualityEvaluator:
    """Evaluate a report against its context and the configured gates."""

    def __init__(
        self, settings: Settings | None = None, llm: TrackedLLM | None = None
    ) -> None:
        self._settings = settings or get_settings()
        self._llm = llm

    async def evaluate(
        self,
        report: IntelligenceReport,
        context: UnifiedResearchContext,
        *,
        now: datetime | None = None,
    ) -> ReportQualityMetrics:
        metrics = self.evaluate_deterministic(report, context, now=now)
        if self._llm is not None and self._settings.intelligence_enable_llm_evaluation:
            await self._judge(report, context, metrics)
        return metrics

    # -- deterministic ---------------------------------------------------------------

    def evaluate_deterministic(
        self,
        report: IntelligenceReport,
        context: UnifiedResearchContext,
        *,
        now: datetime | None = None,
    ) -> ReportQualityMetrics:
        now = now or utc_now()
        by_id = context.evidence_by_id()
        source_urls = {normalize_url(s.url) for s in context.sources}
        jobs = set(context.job_ids)
        issues: list[CitationIssue] = []
        factual = covered = unsupported = invalid = 0
        support_scores: list[float] = []
        cited_valid: dict[str, EvidenceItem] = {}

        for st in collect_statements(report):
            is_factual = st.kind in _FACTUAL
            valid_items: list[EvidenceItem] = []
            for eid in st.evidence_ids:
                problem = self._citation_problem(eid, by_id, jobs, source_urls, context)
                if problem:
                    invalid += 1
                    issues.append(
                        CitationIssue(
                            location=st.location,
                            statement=st.text[:200],
                            evidence_id=eid,
                            problem=problem,
                        )
                    )
                else:
                    valid_items.append(by_id[eid])
                    cited_valid[eid] = by_id[eid]
            if not is_factual:
                continue
            factual += 1
            problems: list[str] = []
            if not st.evidence_ids:
                problems.append("no_citation")
            elif not valid_items:
                problems.append("no_valid_citation")
            else:
                covered += 1
                haystack = terms(
                    " ".join(
                        f"{e.entity or ''} {e.text} {e.excerpt}" for e in valid_items
                    )
                )
                support = overlap(terms(st.text), haystack)
                support_scores.append(support)
                if support < SUPPORT_THRESHOLD:
                    problems.append("weak_support")
                evidence_numbers = set()
                for e in valid_items:
                    evidence_numbers |= numbers_in(f"{e.text} {e.excerpt}")
                if numbers_in(st.text) - evidence_numbers:
                    problems.append("unsupported_figure")
            if problems:
                unsupported += 1
                for p in problems:
                    if p in ("no_valid_citation",):
                        continue  # already reported per invalid citation
                    issues.append(
                        CitationIssue(
                            location=st.location, statement=st.text[:200], problem=p
                        )
                    )

        undisclosed = self._undisclosed_contradictions(report, context)
        critical_missing = self._undisclosed_gaps(report, context)
        missing_sections = self._missing_sections(report, bool(context.evidence))
        cited = list(cited_valid.values())
        horizon = self._settings.intelligence_freshness_days
        dated = [_aware(e.observed_at) for e in cited if e.observed_at is not None]
        fresh = (
            round(
                sum(1 for d in dated if (now - d).days <= horizon) / len(dated),
                3,
            )
            if dated
            else None
        )
        m = ReportQualityMetrics(
            citation_coverage=round(covered / factual, 3) if factual else 0.0,
            evidence_coverage=round(len(cited_valid) / len(context.evidence), 3)
            if context.evidence
            else 0.0,
            unsupported_claim_count=unsupported,
            invalid_citation_count=invalid,
            contradiction_count=len(report.contradictions),
            undisclosed_contradiction_count=undisclosed,
            relevance_score=round(sum(support_scores) / len(support_scores), 3)
            if support_scores
            else 0.0,
            completeness_score=round(
                1
                - len(missing_sections)
                / max(1, len(self._required(report, bool(context.evidence)))),
                3,
            ),
            source_diversity=round(
                min(1.0, len({registrable_domain(e.source_domain) for e in cited}) / 3),
                3,
            ),
            source_freshness=fresh,
            structural_validity=not missing_sections,
            missing_sections=missing_sections,
            factual_statements=factual,
            issues=issues,
        )
        self._apply_gates(m, report, critical_missing)
        return m

    @staticmethod
    def _citation_problem(
        eid: str,
        by_id: dict[str, EvidenceItem],
        jobs: set,
        source_urls: set[str],
        context: UnifiedResearchContext,
    ) -> str | None:
        item = by_id.get(eid)
        if item is None:
            return "missing_reference"
        if item.research_job_id not in jobs:
            return "wrong_job"
        if normalize_url(item.source_url) not in source_urls:
            return "missing_source"
        text = context.source_texts.get(item.source_url)
        if text is not None and not contains_excerpt(text, item.excerpt):
            return "excerpt_not_found"
        return None

    @staticmethod
    def _undisclosed_contradictions(
        report: IntelligenceReport, context: UnifiedResearchContext
    ) -> int:
        disclosed = [set(c.evidence_ids) for c in report.contradictions]
        return sum(
            1
            for c in context.conflicts
            if not any(len(set(c.evidence_ids) & d) >= 2 for d in disclosed)
        )

    @staticmethod
    def _undisclosed_gaps(
        report: IntelligenceReport, context: UnifiedResearchContext
    ) -> int:
        reported = {(g.related_entity, g.predicate) for g in report.information_gaps}
        return sum(
            1
            for g in context.missing_information
            if g.critical and (g.related_entity, g.predicate) not in reported
        )

    @staticmethod
    def _required(report: IntelligenceReport, has_evidence: bool = True) -> list[str]:
        """Sections that must be non-empty. Content sections are only required
        when there is evidence to put in them (an empty-evidence report must
        say so in its limitations rather than fail structurally)."""
        required = ["methodology", "scope", "limitations"]
        if has_evidence:
            required += ["executive_summary", "key_findings", "sources"]
            if report.report_type in COMPARISON_TYPES:
                required.append("comparisons")
        return required

    def _missing_sections(
        self, report: IntelligenceReport, has_evidence: bool
    ) -> list[str]:
        return [
            name
            for name in self._required(report, has_evidence)
            if not getattr(report, name)
        ]

    def _apply_gates(
        self, m: ReportQualityMetrics, report: IntelligenceReport, undisclosed_gaps: int
    ) -> None:
        s = self._settings
        failures: list[str] = []
        review: list[str] = []
        if m.invalid_citation_count > 0:
            failures.append(f"{m.invalid_citation_count} invalid citation(s)")
        if not m.structural_validity:
            failures.append("missing sections: " + ", ".join(m.missing_sections))
        if m.unsupported_claim_count > s.intelligence_max_unsupported_claims:
            review.append(
                f"{m.unsupported_claim_count} unsupported factual statement(s)"
            )
        if (
            m.factual_statements
            and m.citation_coverage < s.intelligence_min_citation_coverage
        ):
            review.append(
                f"citation coverage {m.citation_coverage:.0%} below {s.intelligence_min_citation_coverage:.0%}"
            )
        if m.undisclosed_contradiction_count:
            review.append(
                f"{m.undisclosed_contradiction_count} contradiction(s) not disclosed"
            )
        if undisclosed_gaps:
            review.append(
                f"{undisclosed_gaps} critical information gap(s) not disclosed"
            )
        if report.metadata.get("llm_degraded"):
            review.append("LLM analysis was incomplete (degraded run)")
        if report.metadata.get("mock_provider"):
            review.append("mock provider: content is not real analysis")
        m.gate_failures = [*failures, *review]
        m.overall_status = (
            QualityStatus.FAILED
            if failures
            else QualityStatus.REQUIRES_REVIEW
            if review
            else QualityStatus.PASSED
        )

    # -- optional LLM judge --------------------------------------------------------------

    async def _judge(
        self,
        report: IntelligenceReport,
        context: UnifiedResearchContext,
        metrics: ReportQualityMetrics,
    ) -> None:
        assert self._llm is not None
        by_id = context.evidence_by_id()
        statements = [
            s
            for s in collect_statements(report)
            if s.kind in _FACTUAL and s.evidence_ids
        ][:25]
        cited_ids = list(dict.fromkeys(e for s in statements for e in s.evidence_ids))
        evidence = [by_id[e] for e in cited_ids if e in by_id]
        listing = "\n".join(
            f"{s.location}: {s.text} [{', '.join(s.evidence_ids)}]" for s in statements
        )
        t = TEMPLATES["quality_evaluation"]
        try:
            verdict = await self._llm.structured(
                build_prompt(t, report.objective, evidence, report_statements=listing),
                JudgeOutput,
                system=SYSTEM_PROMPT,
                operation="quality_evaluation",
                prompt_version=f"{PROMPT_VERSION}/{t.name}@{t.version}",
            )
        except ProviderError as exc:
            metrics.llm_judgement = {"skipped": exc.code}
            return
        metrics.llm_judgement = {
            "note": verdict.note,
            "issue_count": len(verdict.issues),
        }
        for issue in verdict.issues:
            metrics.issues.append(
                CitationIssue(
                    location=issue.location,
                    statement="",
                    problem=f"llm_review: {issue.problem}",
                    severity="warning",
                )
            )
        if verdict.issues and metrics.overall_status is QualityStatus.PASSED:
            metrics.overall_status = QualityStatus.REQUIRES_REVIEW
            metrics.gate_failures.append(
                f"LLM reviewer raised {len(verdict.issues)} issue(s)"
            )


def _aware(value: datetime) -> datetime:
    from datetime import UTC

    return value if value.tzinfo else value.replace(tzinfo=UTC)
