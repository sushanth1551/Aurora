"""Analysis orchestrator: context -> reasoning -> synthesis -> report -> quality.

Runs **synchronously** inside the request, bounded by
``INTELLIGENCE_MAX_DURATION_SECONDS``. The analysis row is committed at each
stage, so ``GET`` endpoints show progress and a failed run keeps its context and
usage records. No queue or worker exists; nothing here claims background
execution.

Status rules
------------
* ``failed``              no usable evidence, provider not configured mid-run,
                          deadline exceeded, or an unexpected error.
* ``partially_completed`` the LLM was unavailable/over budget for part of the run;
                          the deterministic parts are still delivered.
* ``requires_review``     the report was produced but a quality gate did not pass
                          (always the case for the mock provider).
* ``completed``           every stage ran and all quality gates passed.
"""

import asyncio
import logging
import time
from collections.abc import Callable
from typing import Any
from uuid import UUID, uuid4

from app.core.config import Settings, get_settings
from app.core.exceptions import (
    AuroraException,
    ConfigurationException,
    ResourceNotFoundException,
)
from app.core.logging import log_event
from app.database.repositories.analysis_repository import AnalysisRepository
from app.intelligence.context_builder import ContextBuilder
from app.intelligence.models import (
    AnalysisStatus,
    IntelligenceReport,
    QualityStatus,
)
from app.intelligence.prompts import PROMPT_VERSION
from app.intelligence.quality_evaluator import QualityEvaluator
from app.intelligence.reasoning_engine import (
    FollowUpExecutor,
    ReasoningEngine,
    ReasoningLimits,
)
from app.intelligence.report_generator import ReportGenerator, render_markdown
from app.intelligence.synthesis_engine import SynthesisEngine
from app.llm.base import LLMProvider
from app.llm.exceptions import ProviderError
from app.llm.provider_factory import FallbackProvider, build_llm_provider
from app.llm.usage_tracker import PricingRegistry, TrackedLLM, UsageBudget, UsageTracker
from app.schemas.analysis import AnalysisProgress, AnalysisResponse, AnalyzeRequest

logger = logging.getLogger(__name__)

#: Seconds beyond the soft deadline before the whole run is cancelled.
HARD_DEADLINE_GRACE = 30.0

ProviderBuilder = Callable[..., LLMProvider]


def effective_context_budget(
    provider: LLMProvider, settings: Settings
) -> tuple[int, str]:
    """Evidence-token budget for this run and a label saying where it came from.

    With provider fallback enabled the context is built once and may be answered
    by either provider, so the budget is the smaller of the two: a prompt sized
    for the primary must still fit the fallback.
    """
    if isinstance(provider, FallbackProvider):
        primary = settings.context_budget_for(provider.primary.name)
        secondary = settings.context_budget_for(provider.secondary.name)
        if secondary < primary:
            return secondary, f"{provider.secondary.name} (fallback, smaller)"
        return primary, provider.primary.name
    return settings.context_budget_for(provider.name), provider.name


class IntelligenceAnalysisService:
    """Run and retrieve analyses with injected collaborators."""

    def __init__(
        self,
        *,
        repository: AnalysisRepository,
        builder: ContextBuilder,
        executor: FollowUpExecutor | None = None,
        provider_builder: ProviderBuilder = build_llm_provider,
        settings: Settings | None = None,
        pricing: PricingRegistry | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._repo = repository
        self._builder = builder
        self._executor = executor
        self._provider_builder = provider_builder
        self._pricing = pricing or PricingRegistry.from_settings(self._settings)

    # -- run ------------------------------------------------------------------------

    async def analyze(self, request: AnalyzeRequest) -> AnalysisResponse:
        s = self._settings
        if not await self._repo.job_exists(request.research_job_id):
            raise ResourceNotFoundException(
                f"Research job {request.research_job_id} was not found."
            )
        provider = self._provider_builder(
            s,
            preferred_provider=request.preferred_provider,
            preferred_model=request.preferred_model,
            pricing_models=self._pricing.keys(),
            allow_override=True,
        )
        max_iterations = min(
            request.maximum_iterations or 99, s.intelligence_max_iterations
        )
        max_sources = min(request.maximum_sources or 10**6, s.intelligence_max_sources)
        follow_up = (
            request.allow_follow_up_research
            and s.intelligence_enable_followup_research
            and self._executor is not None
        )
        context_budget, budget_source = effective_context_budget(provider, s)
        analysis_id = await self._repo.create_analysis(
            research_job_id=request.research_job_id,
            objective=request.research_objective,
            analysis_type=request.analysis_type,
            provider=provider.name,
            model=provider.model,
            configuration={
                "max_iterations": max_iterations,
                "max_sources": max_sources,
                "follow_up_research": follow_up,
                "output_format": request.output_format,
                "prompt_version": PROMPT_VERSION,
                "budget": UsageBudget.from_settings(s).model_dump(),
                "context_token_budget": context_budget,
                "context_budget_source": budget_source,
            },
            analysis_id=uuid4(),
        )
        tracker = UsageTracker(
            pricing=self._pricing,
            budget=UsageBudget.from_settings(s),
            research_job_id=request.research_job_id,
            analysis_id=analysis_id,
        )
        started = time.monotonic()
        try:
            await asyncio.wait_for(
                self._run(
                    analysis_id, request, provider, tracker,
                    max_iterations, max_sources, follow_up, started,
                    context_budget, budget_source,
                ),
                timeout=s.intelligence_max_duration_seconds + HARD_DEADLINE_GRACE,
            )  # fmt: skip
        except TimeoutError:
            await self._fail(analysis_id, "deadline_exceeded", tracker)
        except ProviderError as exc:
            await self._fail(analysis_id, exc.code, tracker)
        except ConfigurationException as exc:
            await self._fail(analysis_id, exc.code, tracker)
        except AuroraException:
            await self._persist_usage(analysis_id, tracker)
            raise
        except Exception:
            logger.exception("analysis_failed analysis_id=%s", analysis_id)
            await self._fail(analysis_id, "internal_error", tracker)
            raise
        result = await self._repo.get_analysis(analysis_id)
        assert result is not None
        return result

    async def _run(
        self,
        analysis_id: UUID,
        request: AnalyzeRequest,
        provider: LLMProvider,
        tracker: UsageTracker,
        max_iterations: int,
        max_sources: int,
        follow_up: bool,
        started: float,
        context_budget: int,
        budget_source: str,
    ) -> None:
        s = self._settings
        llm = TrackedLLM(provider, tracker)
        progress = AnalysisProgress(
            stage="context",
            context_token_budget=context_budget,
            context_budget_source=budget_source,
        )

        async def push(stage: str | None = None, **info: Any) -> None:
            if stage:
                progress.stage = stage
            progress.llm_calls = tracker.calls
            progress.tokens_used = tracker.tokens_used
            progress.estimated_cost = tracker.cost_used
            progress.cost_complete = tracker.cost_complete
            for key, value in info.items():
                setattr(progress, key, value)
            await self._repo.update_analysis(analysis_id, progress=progress)

        await self._repo.update_analysis(
            analysis_id, status=AnalysisStatus.RUNNING, progress=progress
        )
        log_event(
            logger,
            "analysis_started",
            analysis_id=analysis_id,
            job_id=request.research_job_id,
            provider=provider.name,
        )

        context = await self._builder.build(
            [request.research_job_id],
            request.research_objective,
            max_sources=max_sources,
            max_tokens=context_budget,
        )
        await self._repo.save_context(analysis_id, context)
        await push("reasoning", **self._context_metrics(context))
        if not context.evidence:
            await self._repo.update_analysis(
                analysis_id,
                status=AnalysisStatus.FAILED,
                error="The research job has no usable evidence to analyse.",
                completed=True,
            )
            await self._persist_usage(analysis_id, tracker)
            return

        engine = ReasoningEngine(
            llm, self._builder, self._executor if follow_up else None
        )
        limits = ReasoningLimits(
            max_iterations=max_iterations,
            max_followup_per_iteration=s.intelligence_max_followup_queries,
            max_total_queries=s.intelligence_max_total_queries,
            deadline=started + s.intelligence_max_duration_seconds,
            max_sources=max_sources,
            max_tokens=context_budget,
            allow_followup=follow_up,
        )

        async def on_progress(stage: str, info: dict) -> None:
            await push("reasoning", **{"notes": [f"{stage}: {info}"[:200]]})

        reasoning, context = await engine.run(context, limits, on_progress)
        if context.version > 1:
            await self._repo.save_context(analysis_id, context)
        executed = len([q for q in reasoning.followups if q.status.value == "executed"])
        await push(
            "synthesis",
            iterations=reasoning.iterations,
            follow_up_queries=executed,
            stop_reason=reasoning.stop_reason,
            **self._context_metrics(context),
        )

        usable = any(r.success for r in tracker.records)
        synthesis_llm = llm if usable or not tracker.records else None
        synthesis = await SynthesisEngine(synthesis_llm).synthesize(
            context, reasoning, request.analysis_type
        )

        await push("reporting")
        report = ReportGenerator().build(
            analysis_id=analysis_id,
            analysis_type=request.analysis_type,
            context=context,
            reasoning=reasoning,
            synthesis=synthesis,
            provider=provider.name,
            model=provider.model,
            usage=tracker.snapshot(),
        )
        quality = await QualityEvaluator(s, llm).evaluate(report, context)
        report.quality = quality
        markdown = render_markdown(report)

        degraded = reasoning.llm_degraded or synthesis.llm_degraded
        if degraded:
            status = AnalysisStatus.PARTIALLY_COMPLETED
        elif quality.overall_status is not QualityStatus.PASSED:
            status = AnalysisStatus.REQUIRES_REVIEW
        else:
            status = AnalysisStatus.COMPLETED
        await self._repo.save_reasoning(analysis_id, reasoning, synthesis)
        await self._repo.save_report(analysis_id, report, markdown, quality)
        await self._persist_usage(analysis_id, tracker)
        progress.degraded = degraded
        progress.notes = quality.gate_failures[:10]
        await push("completed")
        await self._repo.update_analysis(
            analysis_id,
            status=status,
            iteration_count=reasoning.iterations,
            prompt_version=PROMPT_VERSION,
            completed=True,
        )
        log_event(
            logger,
            "analysis_finished",
            analysis_id=analysis_id,
            status=status.value,
            quality=quality.overall_status.value,
            citation_coverage=quality.citation_coverage,
            iterations=reasoning.iterations,
            evidence=len(context.evidence),
            tokens=tracker.tokens_used,
            duration_s=round(time.monotonic() - started, 2),
        )

    @staticmethod
    def _context_metrics(context: Any) -> dict[str, Any]:
        """Progress fields describing what fit in the evidence budget."""
        stats = context.statistics
        return {
            "evidence_items": len(context.evidence),
            "sources": len(context.sources),
            "context_tokens": context.token_estimate,
            "evidence_considered": int(
                stats.get("evidence_considered", len(context.evidence))
            ),
            "evidence_trimmed": bool(stats.get("truncated", False)),
        }

    async def _persist_usage(self, analysis_id: UUID, tracker: UsageTracker) -> None:
        """Save records not yet persisted (idempotent; totals stay in the tracker)."""
        pending = tracker.records[tracker.persisted :]
        try:
            if pending:
                await self._repo.save_usage(analysis_id, pending)
                tracker.persisted += len(pending)
        except AuroraException:
            logger.error("usage_persist_failed analysis_id=%s", analysis_id)

    async def _fail(
        self, analysis_id: UUID, reason: str, tracker: UsageTracker
    ) -> None:
        log_event(
            logger,
            "analysis_failed",
            level=logging.WARNING,
            analysis_id=analysis_id,
            reason=reason,
        )
        await self._persist_usage(analysis_id, tracker)
        await self._repo.update_analysis(
            analysis_id,
            status=AnalysisStatus.FAILED,
            error=f"Analysis failed: {reason}",
            completed=True,
        )

    # -- reads ----------------------------------------------------------------------

    async def latest(self, job_id: UUID) -> AnalysisResponse:
        found = await self._repo.latest_analysis(job_id)
        if found is None:
            raise ResourceNotFoundException(f"No analysis exists for job {job_id}.")
        return found

    async def _analysis_id(self, job_id: UUID, analysis_id: UUID | None) -> UUID:
        if analysis_id is not None:
            found = await self._repo.get_analysis(analysis_id)
            if found is None or found.research_job_id != job_id:
                raise ResourceNotFoundException("Analysis not found for this job.")
            return analysis_id
        return (await self.latest(job_id)).analysis_id

    async def report(
        self, job_id: UUID, analysis_id: UUID | None = None
    ) -> tuple[IntelligenceReport, str]:
        found = await self._repo.get_report(
            await self._analysis_id(job_id, analysis_id)
        )
        if found is None:
            raise ResourceNotFoundException("No report has been generated yet.")
        return found

    async def usage(self, job_id: UUID, analysis_id: UUID | None = None):
        found = await self._repo.get_usage(await self._analysis_id(job_id, analysis_id))
        if found is None:
            raise ResourceNotFoundException("Analysis not found.")
        return found

    async def quality(self, job_id: UUID, analysis_id: UUID | None = None):
        found = await self._repo.get_quality(
            await self._analysis_id(job_id, analysis_id)
        )
        if found is None:
            raise ResourceNotFoundException("No quality evaluation exists yet.")
        return found
