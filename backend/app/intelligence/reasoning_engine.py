"""Bounded research-reasoning loop.

Per iteration: analyse the evidence (findings, gaps, contradictions), decide
whether more research would materially help and, if so and permitted, validate
and execute a few follow-up queries through the existing Module 2/3 services,
then rebuild the context. Stopping conditions are explicit and every loop path
ends in one of: ``sufficient``, ``no_gaps``, ``max_iterations``, ``max_duration``,
``budget_exhausted``, ``followup_disabled``, ``no_valid_followups``,
``followup_failed``, ``llm_error``.

The model proposes; this engine disposes. Every cited evidence ID is checked
against the context, follow-up queries are schema-, length- and scope-validated,
and the model never touches URLs or tools. Only short rationales are stored,
never reasoning traces.
"""

import asyncio
import logging
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from app.core.logging import log_event
from app.intelligence.confidence import compute_confidence
from app.intelligence.context_builder import ContextBuilder
from app.intelligence.llm_schemas import (
    AnalysisOutput,
    FollowupOutput,
    QuestionsOutput,
)
from app.intelligence.models import (
    Contradiction,
    EvidenceItem,
    FindingKind,
    FollowUpResearchQuery,
    InformationGap,
    ItemStatus,
    ReasoningResult,
    ResearchDecision,
    ResearchFinding,
    ResearchQuestion,
    UnifiedResearchContext,
)
from app.intelligence.prompts import (
    PROMPT_VERSION,
    SYSTEM_PROMPT,
    TEMPLATES,
    build_prompt,
)
from app.intelligence.text import is_ranking_claim, overlap, terms
from app.llm.exceptions import LLMBudgetError, ProviderError
from app.llm.usage_tracker import TrackedLLM

logger = logging.getLogger(__name__)

MAX_QUERY_CHARS = 200
_UNSAFE_QUERY = re.compile(
    r"(://|www\.|[<>{}|\\`]|\bignore (all |the )?(previous|above)\b|\bsystem prompt\b)",
    re.IGNORECASE,
)
DEFAULT_QUESTIONS = (
    "What products and services does each company offer?",
    "What pricing is publicly documented for each company?",
    "Which customer segments does each company target?",
    "Which technologies and integrations does each company document?",
)

Progress = Callable[[str, dict], Awaitable[None]]


class FollowUpExecutor(Protocol):
    """Runs one validated follow-up query via Module 2/3 and returns its job id."""

    async def execute(self, query: FollowUpResearchQuery) -> UUID: ...


@dataclass(frozen=True)
class ReasoningLimits:
    max_iterations: int
    max_followup_per_iteration: int
    max_total_queries: int
    deadline: float  # absolute time.monotonic() value
    max_sources: int | None = None
    #: Evidence-token budget used for every context rebuild (provider-specific).
    max_tokens: int | None = None
    allow_followup: bool = False


def validate_followup(
    item: FollowUpResearchQuery,
    context: UnifiedResearchContext,
    executed: list[FollowUpResearchQuery],
) -> str | None:
    """Return a rejection reason, or ``None`` if the query is acceptable."""
    query = " ".join(item.query.split())
    if not 3 <= len(query) <= MAX_QUERY_CHARS:
        return f"query must be 3-{MAX_QUERY_CHARS} characters"
    if _UNSAFE_QUERY.search(query) or any(
        ord(ch) < 32 for ch in item.query if ch != "\n"
    ):
        return "query contains URLs, markup or instruction-like text"
    if item.target_name and (
        len(item.target_name) > 120 or _UNSAFE_QUERY.search(item.target_name)
    ):
        return "target name is invalid"
    if any(query.casefold() == e.query.casefold() for e in executed):
        return "duplicate of an earlier query"
    scope = terms(" ".join([context.objective, *context.companies, *context.products]))
    if overlap(terms(query), scope) == 0:
        return "query is outside the research scope"
    item.query = query
    return None


class ReasoningEngine:
    """Question generation, evidence analysis and bounded follow-up research."""

    def __init__(
        self,
        llm: TrackedLLM,
        builder: ContextBuilder,
        executor: FollowUpExecutor | None = None,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._llm = llm
        self._builder = builder
        self._executor = executor
        self._clock = clock

    async def run(
        self,
        context: UnifiedResearchContext,
        limits: ReasoningLimits,
        on_progress: Progress | None = None,
    ) -> tuple[ReasoningResult, UnifiedResearchContext]:
        result = ReasoningResult()
        executed: list[FollowUpResearchQuery] = []
        result.questions = await self._questions(context, result)

        for iteration in range(1, limits.max_iterations + 1):
            result.iterations = iteration
            if self._clock() >= limits.deadline:
                self._stop(result, iteration, "max_duration", "Time limit reached.")
                break
            if on_progress:
                await on_progress("analysis", {"iteration": iteration})
            analysis = await self._analyse(context, result)
            self._absorb(context, result, analysis)
            if analysis is None:
                self._stop(
                    result,
                    iteration,
                    result.stop_reason or "llm_error",
                    "Analysis could not be completed by the LLM.",
                )
                break
            if analysis.sufficient or not result.gaps:
                reason = "sufficient" if analysis.sufficient else "no_gaps"
                self._stop(result, iteration, reason, analysis.sufficiency_rationale)
                break
            if not limits.allow_followup or self._executor is None:
                self._stop(
                    result,
                    iteration,
                    "followup_disabled",
                    "Gaps remain; follow-up research is not enabled.",
                )
                break
            if iteration == limits.max_iterations:
                self._stop(
                    result, iteration, "max_iterations", "Iteration limit reached."
                )
                break
            budget_left = limits.max_total_queries - len(executed)
            quota = min(limits.max_followup_per_iteration, budget_left)
            if quota <= 0:
                self._stop(result, iteration, "max_queries", "Query limit reached.")
                break
            accepted = await self._propose(context, result, executed, quota)
            if not accepted:
                self._stop(
                    result,
                    iteration,
                    result.stop_reason or "no_valid_followups",
                    "No acceptable follow-up queries.",
                )
                break
            new_jobs = await self._execute(accepted, limits, on_progress)
            executed.extend(accepted)
            result.followups.extend(accepted)
            if not new_jobs:
                self._stop(
                    result, iteration, "followup_failed", "Follow-up research failed."
                )
                break
            result.decisions.append(
                ResearchDecision(
                    iteration=iteration,
                    decision="continue",
                    rationale=f"Ran {len(new_jobs)} follow-up quer"
                    f"{'y' if len(new_jobs) == 1 else 'ies'} to fill {len(result.gaps)} gap(s).",
                )
            )
            context = await self._builder.build(
                [*context.job_ids, *new_jobs],
                context.objective,
                max_sources=limits.max_sources,
                max_tokens=limits.max_tokens,
                version=context.version + 1,
            )

        self._finalise_questions(result)
        log_event(
            logger,
            "reasoning_finished",
            iterations=result.iterations,
            stop_reason=result.stop_reason,
            followups=len(result.followups),
            findings=len(result.findings),
        )
        return result, context

    # -- steps -----------------------------------------------------------------------

    async def _ask[M](
        self,
        schema: type[M],
        template: str,
        context: UnifiedResearchContext,
        **sections: str,
    ) -> M:
        t = TEMPLATES[template]
        prompt = build_prompt(t, context.objective, context.evidence, **sections)
        return await self._llm.structured(  # type: ignore[return-value, type-var]
            prompt,
            schema,  # type: ignore[arg-type]
            system=SYSTEM_PROMPT,
            operation=template,
            prompt_version=f"{PROMPT_VERSION}/{t.name}@{t.version}",
        )

    async def _questions(
        self, context: UnifiedResearchContext, result: ReasoningResult
    ) -> list[ResearchQuestion]:
        questions: list[ResearchQuestion] = []
        try:
            out = await self._ask(QuestionsOutput, "question_generation", context)
            questions = [
                ResearchQuestion(
                    question=q.question,
                    related_entity=q.related_entity,
                    reason=q.reason,
                    priority=q.priority,
                )
                for q in out.questions
            ]
        except ProviderError as exc:
            self._note_llm_failure(result, "question generation", exc)
        if not questions:
            questions = [
                ResearchQuestion(question=q, reason="default question", priority=3)
                for q in DEFAULT_QUESTIONS
            ]
        for q in questions:
            q.evidence_ids = self._map_evidence(q.question, context.evidence)
        return questions

    @staticmethod
    def _map_evidence(question: str, evidence: list[EvidenceItem]) -> list[str]:
        wanted = terms(question)
        scored = sorted(
            (
                (
                    overlap(
                        wanted, terms(f"{e.entity or ''} {e.predicate or ''} {e.text}")
                    ),
                    e,
                )
                for e in evidence
            ),
            key=lambda pair: (-pair[0], pair[1].evidence_id),
        )
        return [e.evidence_id for s, e in scored[:3] if s > 0]

    async def _analyse(
        self, context: UnifiedResearchContext, result: ReasoningResult
    ) -> AnalysisOutput | None:
        numbered = "\n".join(
            f"Q{i}: {q.question}" for i, q in enumerate(result.questions, start=1)
        )
        known = "\n".join(
            f"- {g.description}" for g in context.missing_information[:30]
        )
        try:
            return await self._ask(
                AnalysisOutput,
                "evidence_analysis",
                context,
                questions=numbered,
                known_gaps=known or "(none)",
            )
        except LLMBudgetError as exc:
            self._note_llm_failure(result, "evidence analysis", exc)
            result.stop_reason = "budget_exhausted"
        except ProviderError as exc:
            self._note_llm_failure(result, "evidence analysis", exc)
            result.stop_reason = "llm_error"
        return None

    def _absorb(
        self,
        context: UnifiedResearchContext,
        result: ReasoningResult,
        analysis: AnalysisOutput | None,
    ) -> None:
        """Validate model output against the context and store the current view."""
        by_id = context.evidence_by_id()
        findings: list[ResearchFinding] = []
        gaps = list(context.missing_information)
        contradictions = list(context.conflicts)
        if analysis is not None:
            for f in analysis.findings:
                valid = [e for e in dict.fromkeys(f.evidence_ids) if e in by_id]
                dropped = len(f.evidence_ids) - len(valid)
                kind = f.kind
                reason = f.reason
                if kind in (FindingKind.FACT, FindingKind.OBSERVATION) and not valid:
                    kind = FindingKind.HYPOTHESIS
                    reason = (
                        reason + " " if reason else ""
                    ) + "Downgraded: no valid evidence cited."
                if kind is FindingKind.FACT and is_ranking_claim(f.description):
                    kind = FindingKind.HYPOTHESIS
                    reason = (reason + " " if reason else "") + (
                        "Downgraded: ranking/superlative wording is not supported "
                        "by comparable evidence."
                    )
                if dropped:
                    reason = (
                        reason + " " if reason else ""
                    ) + f"{dropped} unknown evidence id(s) removed."
                findings.append(
                    ResearchFinding(
                        description=f.description,
                        kind=kind,
                        related_entity=f.related_entity,
                        reason=reason,
                        evidence_ids=valid,
                        confidence=compute_confidence(
                            [by_id[e] for e in valid], f.analytical_confidence
                        ),
                    )
                )
            known_keys = {(g.related_entity, g.predicate) for g in gaps}
            for g in analysis.gaps:
                key = (g.related_entity, g.predicate)
                if g.predicate and key in known_keys:
                    continue
                gaps.append(
                    InformationGap(
                        description=g.description,
                        related_entity=g.related_entity,
                        predicate=g.predicate,
                        reason=g.reason,
                        priority=g.priority,
                        origin="llm",
                    )
                )
            have = {frozenset(c.evidence_ids) for c in contradictions}
            for c in analysis.contradictions:
                valid = list(dict.fromkeys(e for e in c.evidence_ids if e in by_id))
                if len(valid) < 2 or frozenset(valid) in have:
                    continue
                items = [by_id[e] for e in valid]
                contradictions.append(
                    Contradiction(
                        subject=c.subject,
                        description=c.description,
                        evidence_ids=valid,
                        claim_ids=[i.claim_id for i in items if i.claim_id],
                        source_urls=sorted({i.source_url for i in items}),
                        origin="llm",
                    )
                )
            answered = {
                n
                for n in analysis.answered_questions
                if 1 <= n <= len(result.questions)
            }
            for n, q in enumerate(result.questions, start=1):
                if n in answered:
                    q.status = ItemStatus.ANSWERED
        result.findings = findings
        result.gaps = gaps
        result.contradictions = contradictions

    async def _propose(
        self,
        context: UnifiedResearchContext,
        result: ReasoningResult,
        executed: list[FollowUpResearchQuery],
        quota: int,
    ) -> list[FollowUpResearchQuery]:
        gap_text = "\n".join(
            f"- [{g.gap_id}] {g.description}"
            for g in sorted(result.gaps, key=lambda g: g.priority)[:15]
        )
        try:
            out = await self._ask(
                FollowupOutput, "followup_queries", context, gaps=gap_text
            )
        except LLMBudgetError as exc:
            self._note_llm_failure(result, "follow-up generation", exc)
            result.stop_reason = "budget_exhausted"
            return []
        except ProviderError as exc:
            self._note_llm_failure(result, "follow-up generation", exc)
            result.stop_reason = "llm_error"
            return []
        accepted: list[FollowUpResearchQuery] = []
        proposed = sorted(out.queries, key=lambda q: q.priority)
        for p in proposed:
            item = FollowUpResearchQuery(
                query=p.query,
                target_name=p.target_name,
                reason=p.reason,
                priority=p.priority,
            )
            rejection = (
                "per-iteration limit"
                if len(accepted) >= quota
                else validate_followup(item, context, [*executed, *accepted])
            )
            if rejection:
                item.status = ItemStatus.REJECTED
                item.rejection_reason = rejection
                result.followups.append(item)
                continue
            accepted.append(item)
        return accepted

    async def _execute(
        self,
        queries: list[FollowUpResearchQuery],
        limits: ReasoningLimits,
        on_progress: Progress | None,
    ) -> list[UUID]:
        assert self._executor is not None
        jobs: list[UUID] = []
        for q in queries:
            remaining = limits.deadline - self._clock()
            if remaining <= 0:
                q.status = ItemStatus.FAILED
                q.rejection_reason = "time limit reached"
                continue
            if on_progress:
                await on_progress("follow_up", {"query_id": q.query_id})
            try:
                q.result_job_id = await asyncio.wait_for(
                    self._executor.execute(q), timeout=remaining
                )
            except Exception as exc:  # noqa: BLE001 - one failed query must not end the run
                q.status = ItemStatus.FAILED
                q.rejection_reason = getattr(exc, "code", type(exc).__name__)
                log_event(
                    logger,
                    "followup_failed",
                    level=logging.WARNING,
                    error=q.rejection_reason,
                )
                continue
            q.status = ItemStatus.EXECUTED
            jobs.append(q.result_job_id)
        return jobs

    # -- bookkeeping ----------------------------------------------------------------------

    @staticmethod
    def _note_llm_failure(
        result: ReasoningResult, step: str, exc: ProviderError
    ) -> None:
        result.llm_degraded = True
        result.uncertainties.append(f"LLM unavailable during {step} ({exc.code}).")

    @staticmethod
    def _stop(
        result: ReasoningResult, iteration: int, reason: str, rationale: str
    ) -> None:
        result.stop_reason = reason
        result.decisions.append(
            ResearchDecision(
                iteration=iteration,
                decision="stop",
                stop_reason=reason,
                rationale=rationale[:400],
            )
        )
        if reason in (
            "max_iterations",
            "max_duration",
            "max_queries",
            "followup_disabled",
        ):
            if result.gaps:
                result.uncertainties.append(
                    f"{len(result.gaps)} information gap(s) remain unresolved ({reason})."
                )

    @staticmethod
    def _finalise_questions(result: ReasoningResult) -> None:
        for q in result.questions:
            if q.status is ItemStatus.OPEN:
                q.status = (
                    ItemStatus.PARTIALLY_ANSWERED
                    if q.evidence_ids
                    else ItemStatus.UNANSWERED
                )
