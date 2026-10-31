"""Multi-source synthesis: deterministic comparison tables plus an LLM analytic layer.

The baseline (comparisons, common features, differentiators) is computed from the
stored claims alone, so a usable, fully cited result exists even when the LLM is
unavailable or over budget. The LLM adds positioning, strengths, trends,
opportunities, threats and conclusions; each statement is validated:

* cited evidence IDs must exist in the context (unknown IDs are removed);
* FACT/OBSERVATION statements with no valid evidence become HYPOTHESIS;
* ranking/superlative wording is downgraded to a hypothesis (no unsupported rankings);
* a trend needs >= 2 valid evidence items from >= 2 distinct sources;
* absent information is reported as missing, never as a weakness.
"""

import logging
from collections import defaultdict

from app.core.logging import log_event
from app.intelligence.confidence import compute_confidence
from app.intelligence.llm_schemas import (
    ComparisonOutput,
    MarketOutput,
    StatementItem,
    StrategicItem,
    SummaryOutput,
)
from app.intelligence.models import (
    AnalysisType,
    CompanyComparison,
    ComparisonDimension,
    CompetitorAnalysis,
    EvidenceItem,
    FindingKind,
    IntelligenceConclusion,
    IntelligenceFinding,
    Level,
    MarketTrend,
    Opportunity,
    ProductComparison,
    ReasoningResult,
    Statement,
    SynthesisResult,
    TechnologyComparison,
    Threat,
    UnifiedResearchContext,
)
from app.intelligence.prompts import (
    PROMPT_VERSION,
    SYSTEM_PROMPT,
    TEMPLATES,
    build_prompt,
)
from app.intelligence.text import is_ranking_claim, normalize_ws
from app.llm.exceptions import ProviderError
from app.llm.usage_tracker import TrackedLLM
from app.schemas.intelligence import EntityType, VerificationStatus

logger = logging.getLogger(__name__)

_VERIFIED = {VerificationStatus.SUPPORTED, VerificationStatus.PARTIALLY_SUPPORTED}
#: predicate -> comparison dimension label
DIMENSIONS = {
    "offers_product": "products",
    "offers_service": "services",
    "price": "pricing",
    "target_customer": "target customers",
    "mentions_technology": "technologies mentioned",
    "integration": "integrations",
    "feature": "features",
    "headquarters": "headquarters",
    "founded_year": "founded",
    "company_size": "company size",
    "use_case": "use cases",
    "industry": "industry",
}
_COMMON_DIMS = {
    "products", "services", "technologies mentioned", "integrations", "features",
    "use cases", "target customers",
}  # fmt: skip
COMPARISON_TEMPLATE = {
    AnalysisType.PRODUCT_COMPARISON: "product_comparison",
    AnalysisType.TECHNOLOGY_ANALYSIS: "technology_analysis",
}


def _statement_for(item: EvidenceItem) -> Statement:
    verified = item.verification_status in _VERIFIED
    return Statement(
        statement=item.text,
        kind=FindingKind.FACT if verified else FindingKind.OBSERVATION,
        evidence_ids=[item.evidence_id],
        entity=item.entity,
        confidence=compute_confidence([item], Level.MEDIUM),
        limitations=(
            []
            if verified
            else [
                "Extracted from the source but not independently verified; "
                "may be a self-reported marketing statement."
            ]
        ),
    )


def build_comparison(
    context: UnifiedResearchContext,
    entity_types: set[EntityType],
    dimensions: set[str],
) -> tuple[list[str], list[ComparisonDimension], list[Statement], list[Statement]]:
    """Per-entity statements for ``dimensions`` plus shared and unique values."""
    rows: dict[str, dict[str, list[EvidenceItem]]] = defaultdict(
        lambda: defaultdict(list)
    )
    entities: set[str] = set()
    for e in context.evidence:
        dim = DIMENSIONS.get(e.predicate or "")
        if (
            dim not in dimensions
            or not e.entity
            or e.verification_status is VerificationStatus.CONFLICTING
            or (e.entity_type is not None and e.entity_type not in entity_types)
        ):
            continue
        rows[dim][e.entity].append(e)
        entities.add(e.entity)
    dims = [
        ComparisonDimension(
            name=name,
            by_entity={
                ent: [_statement_for(i) for i in items]
                for ent, items in sorted(by_entity.items())
            },
        )
        for name, by_entity in sorted(rows.items())
    ]
    common: list[Statement] = []
    unique: list[Statement] = []
    for name, by_entity in sorted(rows.items()):
        if name not in _COMMON_DIMS:
            continue
        by_value: dict[str, dict[str, EvidenceItem]] = defaultdict(dict)
        for ent, items in by_entity.items():
            for i in items:
                by_value[normalize_ws(i.value or i.text)].setdefault(ent, i)
        for _, holders in sorted(by_value.items()):
            items = list(holders.values())
            label = items[0].value or items[0].text
            names = ", ".join(sorted(holders))
            if len(holders) >= 2:
                common.append(
                    Statement(
                        statement=f"{label} ({name}) is documented for: {names}.",
                        kind=FindingKind.OBSERVATION,
                        evidence_ids=[i.evidence_id for i in items],
                        confidence=compute_confidence(items, Level.MEDIUM),
                    )
                )
            elif len(entities) >= 2:
                unique.append(
                    Statement(
                        statement=f"{label} ({name}) is documented only for {names} "
                        "in the collected evidence.",
                        kind=FindingKind.OBSERVATION,
                        evidence_ids=[items[0].evidence_id],
                        entity=names,
                        confidence=compute_confidence(items, Level.LOW),
                        limitations=[
                            "Absence for other entities may reflect missing evidence, "
                            "not a real difference."
                        ],
                    )
                )
    return sorted(entities), dims, common, unique


class SynthesisEngine:
    """Combine evidence into comparisons, findings and conclusions."""

    def __init__(self, llm: TrackedLLM | None) -> None:
        self._llm = llm

    async def synthesize(
        self,
        context: UnifiedResearchContext,
        reasoning: ReasoningResult,
        analysis_type: AnalysisType,
    ) -> SynthesisResult:
        by_id = context.evidence_by_id()
        out = SynthesisResult()
        self._baseline(context, reasoning, out)
        out.findings = [
            IntelligenceFinding(
                statement=f.description,
                kind=f.kind,
                evidence_ids=f.evidence_ids,
                entity=f.related_entity,
                confidence=f.confidence,
                limitations=[f.reason]
                if f.kind is FindingKind.HYPOTHESIS and f.reason
                else [],
                finding_type="analysis",
                related_entities=[f.related_entity] if f.related_entity else [],
            )
            for f in reasoning.findings
        ]
        if self._llm is None:
            out.llm_degraded = True
            out.limitations.append(
                "No LLM was available: only deterministic synthesis."
            )
        else:
            await self._comparison_layer(context, analysis_type, by_id, out)
            await self._market_layer(context, reasoning, by_id, out)
        await self._summary(context, out, by_id)
        return out

    # -- baseline ---------------------------------------------------------------------

    def _baseline(
        self,
        context: UnifiedResearchContext,
        reasoning: ReasoningResult,
        out: SynthesisResult,
    ) -> None:
        names, dims, common, unique = build_comparison(
            context,
            {EntityType.COMPANY, EntityType.PRICING},
            {"products", "services", "pricing", "target customers", "headquarters",
             "founded", "company size", "industry", "technologies mentioned",
             "integrations"},
        )  # fmt: skip
        out.company_comparison = CompanyComparison(
            entities=names,
            dimensions=dims,
            common_features=common,
            differentiators=unique,
            missing_information=[g.description for g in reasoning.gaps],
            conflicts=[c.subject for c in reasoning.contradictions],
        )
        names, dims, common, unique = build_comparison(
            context,
            {EntityType.PRODUCT, EntityType.SERVICE, EntityType.PRICING},
            {"features", "integrations", "pricing", "use cases", "target customers"},
        )
        out.product_comparison = ProductComparison(
            entities=names,
            dimensions=dims,
            common_features=common,
            differentiators=unique,
            missing_information=[
                g.description for g in reasoning.gaps if g.predicate in ("price", None)
            ],
            conflicts=[c.subject for c in reasoning.contradictions],
        )
        names, dims, common, unique = build_comparison(
            context,
            {EntityType.COMPANY, EntityType.PRODUCT, EntityType.TECHNOLOGY},
            {"technologies mentioned"},
        )
        out.technology_comparison = TechnologyComparison(
            entities=names,
            dimensions=dims,
            common_features=common,
            differentiators=unique,
            missing_information=[],
            conflicts=[],
        )
        out.limitations.append(
            "Technologies are recorded as mentions on the subject's pages; a mention "
            "does not prove use."
        )

    # -- validation ----------------------------------------------------------------------

    @staticmethod
    def validate_statement(
        item: StatementItem, by_id: dict[str, EvidenceItem], prefix: str = ""
    ) -> Statement:
        valid = [e for e in dict.fromkeys(item.evidence_ids) if e in by_id]
        limits: list[str] = []
        kind = item.kind
        if len(valid) < len(item.evidence_ids):
            limits.append("Unknown evidence id(s) were removed.")
        if kind in (FindingKind.FACT, FindingKind.OBSERVATION) and not valid:
            kind = FindingKind.HYPOTHESIS
            limits.append("No valid supporting evidence: analytical hypothesis.")
        if kind is FindingKind.FACT and is_ranking_claim(item.statement):
            kind = FindingKind.HYPOTHESIS
            limits.append(
                "Ranking/superlative wording is not supported by comparable evidence."
            )
        cited = [by_id[e] for e in valid]
        return Statement(
            statement=prefix + item.statement,
            kind=kind,
            evidence_ids=valid,
            entity=item.entity,
            confidence=compute_confidence(cited, item.analytical_confidence),
            limitations=limits,
        )

    def _statements(
        self, items: list[StatementItem], by_id: dict[str, EvidenceItem]
    ) -> list[Statement]:
        return [self.validate_statement(i, by_id) for i in items]

    # -- LLM layers -------------------------------------------------------------------------

    async def _ask[M](
        self,
        schema: type[M],
        template: str,
        context: UnifiedResearchContext,
        **sections: str,
    ) -> M:
        assert self._llm is not None
        t = TEMPLATES[template]
        return await self._llm.structured(  # type: ignore[return-value, type-var]
            build_prompt(t, context.objective, context.evidence, **sections),
            schema,  # type: ignore[arg-type]
            system=SYSTEM_PROMPT,
            operation=template,
            prompt_version=f"{PROMPT_VERSION}/{t.name}@{t.version}",
        )

    async def _comparison_layer(
        self,
        context: UnifiedResearchContext,
        analysis_type: AnalysisType,
        by_id: dict[str, EvidenceItem],
        out: SynthesisResult,
    ) -> None:
        template = COMPARISON_TEMPLATE.get(analysis_type, "company_comparison")
        try:
            data = await self._ask(ComparisonOutput, template, context)
        except ProviderError as exc:
            self._degrade(out, "comparison analysis", exc)
            return
        target = {
            "product_comparison": out.product_comparison,
            "technology_analysis": out.technology_comparison,
        }.get(template, out.company_comparison)
        assert target is not None
        target.common_features += self._statements(data.common_features, by_id)
        target.differentiators += self._statements(data.differentiators, by_id)
        competitor = CompetitorAnalysis(
            competitors=target.entities,
            positioning=self._statements(data.positioning, by_id),
            documented_strengths=self._statements(data.documented_strengths, by_id),
            comparative_advantages=self._statements(data.comparative_advantages, by_id),
            observed_limitations=self._statements(data.observed_limitations, by_id),
            hypotheses=[
                s.model_copy(update={"kind": FindingKind.HYPOTHESIS})
                for s in self._statements(data.hypotheses, by_id)
            ],
            missing_public_information=list(target.missing_information),
        )
        if analysis_type is AnalysisType.COMPETITOR_INTELLIGENCE or any(
            [competitor.positioning, competitor.documented_strengths,
             competitor.comparative_advantages, competitor.observed_limitations,
             competitor.hypotheses]
        ):  # fmt: skip
            out.competitor_analysis = competitor
        for kind, items in (
            ("strength", competitor.documented_strengths),
            ("comparative_advantage", competitor.comparative_advantages),
            ("limitation", competitor.observed_limitations),
        ):
            out.findings += [
                IntelligenceFinding(**s.model_dump(), finding_type=kind) for s in items
            ]
        out.limitations += data.limitations

    async def _market_layer(
        self,
        context: UnifiedResearchContext,
        reasoning: ReasoningResult,
        by_id: dict[str, EvidenceItem],
        out: SynthesisResult,
    ) -> None:
        findings = "\n".join(f"- {f.description}" for f in reasoning.findings[:20])
        try:
            data = await self._ask(
                MarketOutput, "market_synthesis", context, findings=findings or "(none)"
            )
        except ProviderError as exc:
            self._degrade(out, "market synthesis", exc)
            return
        for t in data.trends:
            valid = [e for e in dict.fromkeys(t.evidence_ids) if e in by_id]
            sources = {by_id[e].source_url for e in valid}
            if len(valid) < 2 or len(sources) < 2:
                out.limitations.append(
                    "A proposed trend was discarded: it needs at least two observations "
                    "from two different sources."
                )
                continue
            out.trends.append(
                MarketTrend(
                    description=t.description,
                    evidence_ids=valid,
                    observation_count=len(valid),
                    source_count=len(sources),
                    related_entities=t.related_entities,
                    confidence=compute_confidence(
                        [by_id[e] for e in valid], t.analytical_confidence
                    ),
                    limitations=[
                        *t.limitations,
                        "Trend inferred from a small collected sample.",
                    ],
                )
            )
        out.opportunities += [
            self._strategic(Opportunity, t, by_id) for t in data.opportunities
        ]
        out.threats += [self._strategic(Threat, t, by_id) for t in data.threats]
        for c in data.conclusions:
            s = self.validate_statement(c, by_id)
            out.conclusions.append(
                IntelligenceConclusion(
                    **s.model_dump(),
                    finding_ids=[
                        f.finding_id
                        for f in out.findings
                        if set(f.evidence_ids) & set(s.evidence_ids)
                    ],
                )
            )
        out.limitations += data.limitations

    @staticmethod
    def _strategic[T: (Opportunity, Threat)](
        cls: type[T], item: StrategicItem, by_id: dict[str, EvidenceItem]
    ) -> T:
        valid = [e for e in dict.fromkeys(item.evidence_ids) if e in by_id]
        limits = list(item.limitations)
        hypothesis = not valid
        if hypothesis:
            limits.append(
                "No supporting evidence: analytical hypothesis, not a finding."
            )
        return cls(
            description=item.description,
            evidence_ids=valid,
            related_entities=item.related_entities,
            rationale=item.rationale,
            confidence=compute_confidence(
                [by_id[e] for e in valid], item.analytical_confidence
            ),
            limitations=limits,
            is_hypothesis=hypothesis,
        )

    async def _summary(
        self,
        context: UnifiedResearchContext,
        out: SynthesisResult,
        by_id: dict[str, EvidenceItem],
    ) -> None:
        if self._llm is not None and not out.llm_degraded:
            findings = "\n".join(
                f"- {f.statement} [{', '.join(f.evidence_ids)}]"
                for f in out.findings[:20]
            )
            try:
                data = await self._ask(
                    SummaryOutput,
                    "executive_summary",
                    context,
                    findings=findings or "(none)",
                )
                out.executive_summary = self._statements(data.summary, by_id)
                if out.executive_summary:
                    return
            except ProviderError as exc:
                self._degrade(out, "executive summary", exc)
        top = [
            e
            for e in context.evidence
            if e.verification_status in _VERIFIED and e.entity
        ][:5]
        out.executive_summary = [_statement_for(e) for e in top]
        out.limitations.append(
            "Executive summary lists the highest-ranked verified facts; "
            "it was not synthesised by an LLM."
        )

    @staticmethod
    def _degrade(out: SynthesisResult, step: str, exc: ProviderError) -> None:
        out.llm_degraded = True
        out.limitations.append(
            f"LLM {step} unavailable ({exc.code}); result is partial."
        )
        log_event(
            logger,
            "synthesis_degraded",
            level=logging.WARNING,
            step=step,
            error=exc.code,
        )
