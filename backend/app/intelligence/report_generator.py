"""Deterministic report assembly and rendering.

Reports are built from validated schemas only: no model-written HTML, no code
execution, no invented URLs (every citation URL comes from a stored source).
Markdown rendering escapes all text. PDF export is an optional exporter that is
deliberately not implemented (no PDF dependency was needed).
"""

import re
from collections.abc import Iterator
from datetime import datetime
from typing import Any, Protocol
from uuid import UUID

from app.core.exceptions import ConfigurationException
from app.intelligence.models import (
    AnalysisType,
    ComparisonDimension,
    EvidenceItem,
    IntelligenceReport,
    ReasoningResult,
    ReportSourceRef,
    Statement,
    SynthesisResult,
    UnifiedResearchContext,
)
from app.intelligence.prompts import PROMPT_VERSION
from app.schemas.research import utc_now

TITLES = {
    AnalysisType.EXECUTIVE_SUMMARY: "Executive research summary",
    AnalysisType.DETAILED_REPORT: "Detailed research report",
    AnalysisType.COMPANY_INTELLIGENCE: "Company intelligence report",
    AnalysisType.PRODUCT_COMPARISON: "Product comparison report",
    AnalysisType.TECHNOLOGY_ANALYSIS: "Technology analysis report",
    AnalysisType.COMPETITOR_INTELLIGENCE: "Competitor intelligence report",
    AnalysisType.MARKET_RESEARCH: "Market research report",
}
#: Which comparisons each report type includes.
COMPARISONS = {
    AnalysisType.EXECUTIVE_SUMMARY: ("company",),
    AnalysisType.DETAILED_REPORT: ("company", "product", "technology"),
    AnalysisType.COMPANY_INTELLIGENCE: ("company",),
    AnalysisType.PRODUCT_COMPARISON: ("product", "company"),
    AnalysisType.TECHNOLOGY_ANALYSIS: ("technology", "company"),
    AnalysisType.COMPETITOR_INTELLIGENCE: ("company", "product"),
    AnalysisType.MARKET_RESEARCH: ("company",),
}
STANDING_LIMITATIONS = (
    "Findings reflect only the sources collected for this research job at the "
    "research date; sources may be incomplete or outdated.",
    "Company websites are self-reported marketing material, not independent "
    "verification.",
    "Quality checks measure citation structure and support heuristics; they are not "
    "a measure of factual accuracy.",
)
MOCK_WARNING = (
    "Generated with the mock LLM provider: no real analysis was performed. "
    "Do not rely on analytical content."
)


def iter_evidence_ids(node: Any) -> Iterator[str]:
    """Every string under any ``evidence_ids`` key of a dumped report."""
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "evidence_ids" and isinstance(value, list):
                yield from (v for v in value if isinstance(v, str))
            else:
                yield from iter_evidence_ids(value)
    elif isinstance(node, list):
        for value in node:
            yield from iter_evidence_ids(value)


def _source_ref(e: EvidenceItem) -> ReportSourceRef:
    return ReportSourceRef(
        evidence_id=e.evidence_id,
        source_url=e.source_url,
        source_title=e.source_title,
        source_domain=e.source_domain,
        source_kind=e.source_kind,
        excerpt=e.excerpt[:400],
        verification_status=e.verification_status,
        observed_at=e.observed_at,
        credibility=e.credibility.score,
    )


class ReportGenerator:
    """Assemble an ``IntelligenceReport`` and render it."""

    def build(
        self,
        *,
        analysis_id: UUID,
        analysis_type: AnalysisType,
        context: UnifiedResearchContext,
        reasoning: ReasoningResult,
        synthesis: SynthesisResult,
        provider: str,
        model: str,
        usage: dict[str, Any] | None = None,
        now: datetime | None = None,
        version: int = 1,
    ) -> IntelligenceReport:
        wanted = COMPARISONS[analysis_type]
        comparisons: dict[str, Any] = {}
        analyses: dict[str, list[Statement]] = {}
        for key, comparison in (
            ("company", synthesis.company_comparison),
            ("product", synthesis.product_comparison),
            ("technology", synthesis.technology_comparison),
        ):
            if comparison is not None and key in wanted:
                comparisons[key] = comparison.model_dump(mode="json")
            analyses[key] = (
                [*comparison.common_features, *comparison.differentiators]
                if comparison and key in wanted
                else []
            )
        summary_only = analysis_type is AnalysisType.EXECUTIVE_SUMMARY
        mock = provider == "mock"
        limitations = [
            *STANDING_LIMITATIONS,
            *synthesis.limitations,
            *reasoning.uncertainties,
        ]
        if mock:
            limitations.insert(0, MOCK_WARNING)
        stats = context.statistics
        if stats.get("truncated"):
            limitations.append(
                f"Evidence was trimmed to the context budget: "
                f"{stats.get('evidence_selected', len(context.evidence))} of "
                f"{stats.get('evidence_considered', '?')} items "
                f"(~{context.token_estimate} of {stats.get('max_tokens', '?')} tokens) "
                "were analysed; lower-ranked evidence was left out."
            )
        report = IntelligenceReport(
            research_job_id=context.research_job_id,
            analysis_id=analysis_id,
            report_type=analysis_type,
            version=version,
            title=f"{TITLES[analysis_type]}: {context.objective[:90]}",
            objective=context.objective,
            executive_summary=synthesis.executive_summary,
            methodology=self._methodology(context, reasoning, provider, model),
            scope=self._scope(context),
            key_findings=synthesis.findings[: 8 if summary_only else 30],
            company_analysis=analyses["company"],
            product_analysis=analyses["product"],
            technology_analysis=analyses["technology"],
            comparisons=comparisons,
            competitor_analysis=synthesis.competitor_analysis,
            trends=synthesis.trends,
            opportunities=synthesis.opportunities,
            threats=synthesis.threats,
            contradictions=reasoning.contradictions,
            information_gaps=reasoning.gaps,
            conclusions=synthesis.conclusions,
            research_date=now or utc_now(),
            metadata={
                "provider": provider,
                "model": model,
                "prompt_version": PROMPT_VERSION,
                "mock_provider": mock,
                "context_id": str(context.context_id),
                "context_hash": context.context_hash,
                "iterations": reasoning.iterations,
                "stop_reason": reasoning.stop_reason,
                "follow_up_queries": len(
                    [q for q in reasoning.followups if q.status.value == "executed"]
                ),
                "llm_degraded": reasoning.llm_degraded or synthesis.llm_degraded,
                "usage": usage or {},
            },
            limitations=list(dict.fromkeys(limitations)),
        )
        by_id = context.evidence_by_id()
        cited = dict.fromkeys(iter_evidence_ids(report.model_dump(mode="json")))
        for contradiction in report.contradictions:
            cited.update(dict.fromkeys(contradiction.evidence_ids))
        report.sources = [_source_ref(by_id[i]) for i in cited if i in by_id]
        return report

    @staticmethod
    def _methodology(
        context: UnifiedResearchContext,
        reasoning: ReasoningResult,
        provider: str,
        model: str,
    ) -> list[str]:
        s = context.statistics
        return [
            f"Evidence was loaded from {len(context.job_ids)} stored research job(s); "
            f"{s.get('evidence_selected', len(context.evidence))} of "
            f"{s.get('evidence_considered', len(context.evidence))} evidence items from "
            f"{len(context.sources)} source(s) were selected by deterministic relevance and "
            "credibility ranking.",
            f"Analysis used provider '{provider}', model '{model or 'n/a'}', prompt version "
            f"{PROMPT_VERSION}; model output was schema-validated and every citation was "
            "checked against the stored evidence.",
            f"The reasoning loop ran {reasoning.iterations} iteration(s) and stopped because: "
            f"{reasoning.stop_reason or 'n/a'}.",
            "Comparison tables are computed directly from stored claims; the LLM supplied "
            "only the analytical layer (positioning, trends, opportunities, threats).",
        ]

    @staticmethod
    def _scope(context: UnifiedResearchContext) -> list[str]:
        scope = [f"Objective: {context.objective}"]
        for label, values in (
            ("Companies", context.companies),
            ("Products", context.products),
            ("Services", context.services),
            ("Technologies", context.technologies),
        ):
            if values:
                scope.append(
                    f"{label} in evidence ({len(values)}): " + ", ".join(values[:15])
                )
        return scope


# -- Markdown --------------------------------------------------------------------------

_MD_SPECIAL = re.compile(r"([\\`*_{}\[\]<>|#~])")


def md(text: str | None) -> str:
    """Escape text for Markdown so source content cannot inject formatting/HTML."""
    if not text:
        return ""
    return _MD_SPECIAL.sub(r"\\\1", " ".join(str(text).split()))


def _cite(ids: list[str]) -> str:
    return f" _[evidence: {', '.join(ids)}]_" if ids else ""


def _stmt(s: Statement) -> str:
    tag = "" if s.kind.value == "fact" else f" ({s.kind.value})"
    lim = f" — _{md('; '.join(s.limitations))}_" if s.limitations else ""
    conf = f" [confidence: {s.confidence.overall.value}]" if s.confidence else ""
    return f"- {md(s.statement)}{tag}{_cite(s.evidence_ids)}{conf}{lim}"


def _dimension(d: ComparisonDimension) -> list[str]:
    lines = [f"**{md(d.name.title())}**", ""]
    for entity, statements in d.by_entity.items():
        lines.append(f"- {md(entity)}:")
        lines += [f"  {_stmt(s)}" for s in statements]
    return [*lines, ""]


def render_markdown(report: IntelligenceReport) -> str:
    """Human-readable report; deterministic for a given report."""
    q = report.quality
    out: list[str] = [f"# {md(report.title)}", ""]
    out.append(
        f"*Research date:* {report.research_date:%Y-%m-%d} · *Report ID:* {report.report_id}"
    )
    meta = report.metadata
    out.append(
        f"*Provider/model:* {md(str(meta.get('provider')))} / {md(str(meta.get('model')))} · "
        f"*Prompt version:* {md(str(meta.get('prompt_version')))}"
    )
    if q:
        out += ["", f"> **Quality gate: {q.overall_status.value}.** "
                f"Citation coverage {q.citation_coverage:.0%}, invalid citations "
                f"{q.invalid_citation_count}, unsupported claims {q.unsupported_claim_count}. "
                "These are structural heuristics, not a measure of factual accuracy."]  # fmt: skip
    out += ["", "## Research objective", "", md(report.objective), ""]

    def section(title: str, lines: list[str]) -> None:
        if lines:
            out.extend([f"## {title}", "", *lines, ""])

    section("Executive summary", [_stmt(s) for s in report.executive_summary])
    section("Methodology", [f"- {md(m)}" for m in report.methodology])
    section("Scope", [f"- {md(s)}" for s in report.scope])
    section("Key findings", [_stmt(f) for f in report.key_findings])
    section("Company analysis", [_stmt(s) for s in report.company_analysis])
    section("Product analysis", [_stmt(s) for s in report.product_analysis])
    section("Technology analysis", [_stmt(s) for s in report.technology_analysis])
    comparison_lines: list[str] = []
    for key, data in report.comparisons.items():
        comparison_lines += [f"### {key.title()} comparison", ""]
        for raw in data.get("dimensions", []):
            comparison_lines += _dimension(ComparisonDimension.model_validate(raw))
    section("Comparisons", comparison_lines)
    ca = report.competitor_analysis
    if ca:
        lines: list[str] = []
        for title, items in (
            ("Positioning", ca.positioning),
            ("Documented strengths", ca.documented_strengths),
            ("Evidence-supported comparative advantages", ca.comparative_advantages),
            ("Observed limitations", ca.observed_limitations),
            ("Analyst hypotheses", ca.hypotheses),
        ):
            if items:
                lines += [f"**{title}**", "", *[_stmt(s) for s in items], ""]
        if ca.missing_public_information:
            lines += ["**Missing publicly available information**", ""]
            lines += [f"- {md(m)}" for m in ca.missing_public_information]
        section("Competitor analysis", lines)
    section(
        "Market trends",
        [f"- {md(t.description)}{_cite(t.evidence_ids)} ({t.observation_count} observations, "
         f"{t.source_count} sources)" for t in report.trends],
    )  # fmt: skip
    strategic: list[tuple[str, list[Any]]] = [
        ("Opportunities", report.opportunities),
        ("Threats", report.threats),
    ]
    for title, entries in strategic:
        section(
            title,
            [
                f"- {md(i.description)}{' (hypothesis)' if i.is_hypothesis else ''}"
                f"{_cite(i.evidence_ids)} — rationale: {md(i.rationale)}; "
                f"confidence: {i.confidence.overall.value}; limitations: {md('; '.join(i.limitations))}"
                for i in entries
            ],
        )
    section(
        "Contradictions",
        [f"- **{md(c.subject)}**: {md(c.description)}"
         + (f" Values: {md('; '.join(c.values))}." if c.values else "")
         + _cite(c.evidence_ids) for c in report.contradictions],
    )  # fmt: skip
    section(
        "Information gaps",
        [
            f"- {md(g.description)}{' (critical)' if g.critical else ''}"
            for g in report.information_gaps
        ],
    )
    section("Conclusions", [_stmt(c) for c in report.conclusions])
    if q and q.issues:
        section(
            "Quality issues",
            [
                f"- {i.severity}: {md(i.problem)} at {md(i.location)} {md(i.evidence_id or '')}"
                for i in q.issues[:40]
            ],
        )
    section("Limitations", [f"- {md(x)}" for x in report.limitations])
    refs = []
    for r in report.sources:
        label = md(r.source_title or r.source_domain)
        refs.append(
            f"- `{r.evidence_id}` {label} — `{r.source_url}` "
            f"({r.source_kind.value}, {r.verification_status.value})"
        )
    section("Source references", refs)
    return "\n".join(out).rstrip() + "\n"


# -- exporters ---------------------------------------------------------------------------


class ReportExporter(Protocol):
    format: str

    def export(self, report: IntelligenceReport) -> str | bytes: ...


class JsonExporter:
    format = "json"

    def export(self, report: IntelligenceReport) -> str:
        return report.model_dump_json(indent=2)


class MarkdownExporter:
    format = "markdown"

    def export(self, report: IntelligenceReport) -> str:
        return render_markdown(report)


class PdfExporter:
    """Placeholder behind the exporter interface; PDF is intentionally unavailable."""

    format = "pdf"

    def export(self, report: IntelligenceReport) -> bytes:
        raise ConfigurationException(
            "PDF export is not available: no PDF dependency is installed."
        )


EXPORTERS: dict[str, ReportExporter] = {
    "json": JsonExporter(),
    "markdown": MarkdownExporter(),
    "pdf": PdfExporter(),
}
