"""Persistence for the intelligence engine (Module 4).

Stores analyses, context snapshots (references only, not source text), questions,
findings, contradictions, reports, LLM usage and quality evaluations. Prompts and
raw model responses are never stored. Each method commits its own unit of work
and maps database errors to ``PersistenceException``.
"""

import logging
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import (
    IntelligenceAnalysisRecord,
    IntelligenceFindingRecord,
    IntelligenceReportRecord,
    LLMUsageRecord,
    ReportQualityEvaluationRecord,
    ResearchContextRecord,
    ResearchContradictionRecord,
    ResearchJobRecord,
    ResearchQuestionRecord,
)
from app.database.repositories.research_repository import _translate_errors
from app.intelligence.models import (
    AnalysisStatus,
    AnalysisType,
    IntelligenceReport,
    QualityStatus,
    ReasoningResult,
    ReportQualityMetrics,
    SynthesisResult,
    UnifiedResearchContext,
)
from app.llm.usage_tracker import UsageRecord
from app.schemas.analysis import (
    AnalysisProgress,
    AnalysisResponse,
    QualityResponse,
    UsageEntry,
    UsageResponse,
)
from app.schemas.research import utc_now

logger = logging.getLogger(__name__)


class AnalysisRepository:
    """Store and retrieve analyses using an injected async session."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # -- analyses -------------------------------------------------------------------

    async def job_exists(self, job_id: UUID) -> bool:
        async with _translate_errors(self._session):
            row = await self._session.execute(
                select(ResearchJobRecord.id).where(ResearchJobRecord.id == job_id)
            )
            return row.scalar_one_or_none() is not None

    async def create_analysis(
        self,
        *,
        research_job_id: UUID,
        objective: str,
        analysis_type: AnalysisType,
        provider: str,
        model: str,
        configuration: dict[str, Any],
        analysis_id: UUID | None = None,
    ) -> UUID:
        record = IntelligenceAnalysisRecord(
            id=analysis_id or uuid4(),
            research_job_id=research_job_id,
            research_objective=objective,
            analysis_type=analysis_type.value,
            provider=provider,
            model=model,
            status=AnalysisStatus.PENDING.value,
            iteration_count=0,
            started_at=utc_now(),
            configuration=configuration,
            progress=AnalysisProgress().model_dump(mode="json"),
        )
        async with _translate_errors(self._session):
            self._session.add(record)
            await self._session.commit()
        return record.id

    async def update_analysis(
        self,
        analysis_id: UUID,
        *,
        status: AnalysisStatus | None = None,
        progress: AnalysisProgress | None = None,
        iteration_count: int | None = None,
        provider: str | None = None,
        model: str | None = None,
        prompt_version: str | None = None,
        error: str | None = None,
        completed: bool = False,
    ) -> None:
        async with _translate_errors(self._session):
            record = await self._session.get(IntelligenceAnalysisRecord, analysis_id)
            if record is None:
                return
            if status:
                record.status = status.value
            if progress:
                record.progress = progress.model_dump(mode="json")
            if iteration_count is not None:
                record.iteration_count = iteration_count
            if provider:
                record.provider = provider
            if model is not None:
                record.model = model
            if prompt_version:
                record.prompt_version = prompt_version
            if error is not None:
                record.error_summary = error[:1000]
            if completed:
                record.completed_at = utc_now()
            await self._session.commit()

    async def get_analysis(self, analysis_id: UUID) -> AnalysisResponse | None:
        async with _translate_errors(self._session):
            record = await self._session.get(
                IntelligenceAnalysisRecord,
                analysis_id,
                populate_existing=True,
            )
            if record is None:
                return None
            return await self._response(record)

    async def latest_analysis(self, job_id: UUID) -> AnalysisResponse | None:
        async with _translate_errors(self._session):
            rows = (
                (
                    await self._session.execute(
                        select(IntelligenceAnalysisRecord)
                        .where(IntelligenceAnalysisRecord.research_job_id == job_id)
                        .order_by(IntelligenceAnalysisRecord.started_at.desc())
                    )
                )
                .scalars()
                .all()
            )
            if not rows:
                return None
            response = await self._response(rows[0])
            response.other_analysis_ids = [r.id for r in rows[1:]]
            return response

    async def _response(self, r: IntelligenceAnalysisRecord) -> AnalysisResponse:
        report = (
            await self._session.execute(
                select(IntelligenceReportRecord.quality_status)
                .where(IntelligenceReportRecord.analysis_id == r.id)
                .order_by(IntelligenceReportRecord.version.desc())
            )
        ).first()
        return AnalysisResponse(
            analysis_id=r.id,
            research_job_id=r.research_job_id,
            research_objective=r.research_objective,
            analysis_type=AnalysisType(r.analysis_type),
            status=AnalysisStatus(r.status),
            provider=r.provider,
            model=r.model,
            report_status=QualityStatus(report[0]) if report else None,
            report_available=report is not None,
            progress=AnalysisProgress.model_validate(r.progress or {}),
            error=r.error_summary,
            created_at=r.started_at,
            completed_at=r.completed_at,
        )

    # -- results --------------------------------------------------------------------

    async def save_context(
        self, analysis_id: UUID, ctx: UnifiedResearchContext
    ) -> None:
        record = ResearchContextRecord(
            id=ctx.context_id,
            analysis_id=analysis_id,
            version=ctx.version,
            source_references=[
                {"source_id": str(s.source_id) if s.source_id else None, "url": s.url}
                for s in ctx.sources
            ],
            claim_references=[
                {
                    "evidence_id": e.evidence_id,
                    "claim_id": str(e.claim_id) if e.claim_id else None,
                    "source_id": str(e.source_id) if e.source_id else None,
                }
                for e in ctx.evidence
            ],
            statistics=ctx.statistics,
            context_hash=ctx.context_hash,
            token_estimate=ctx.token_estimate,
            created_at=ctx.created_at,
        )
        async with _translate_errors(self._session):
            self._session.add(record)
            await self._session.commit()

    async def save_reasoning(
        self, analysis_id: UUID, reasoning: ReasoningResult, synthesis: SynthesisResult
    ) -> None:
        """Questions, findings and contradictions (short rationales only)."""
        now = utc_now()
        rows: list[Any] = [
            ResearchQuestionRecord(
                id=uuid4(),
                analysis_id=analysis_id,
                question=q.question,
                related_entity=q.related_entity,
                priority=q.priority,
                status=q.status.value,
                evidence_references=q.evidence_ids,
                created_at=q.created_at,
            )
            for q in reasoning.questions
        ]
        rows += [
            IntelligenceFindingRecord(
                id=uuid4(),
                analysis_id=analysis_id,
                finding_type=f.finding_type,
                kind=f.kind.value,
                description=f.statement,
                claim_references=[],
                evidence_references=f.evidence_ids,
                confidence=f.confidence.model_dump(mode="json") if f.confidence else {},
                created_at=now,
            )
            for f in synthesis.findings
        ]
        rows += [
            ResearchContradictionRecord(
                id=uuid4(),
                analysis_id=analysis_id,
                subject=c.subject,
                description=c.description,
                conflicting_claims=[str(i) for i in c.claim_ids],
                sources=c.source_urls,
                resolution_status=c.resolution_status.value,
                created_at=now,
            )
            for c in reasoning.contradictions
        ]
        async with _translate_errors(self._session):
            self._session.add_all(rows)
            await self._session.commit()

    async def save_report(
        self,
        analysis_id: UUID,
        report: IntelligenceReport,
        markdown: str,
        quality: ReportQualityMetrics,
    ) -> UUID:
        """Persist the report and its evaluation; ``(analysis, version)`` is unique,
        so a second save of the same report fails instead of duplicating it."""
        record = IntelligenceReportRecord(
            id=report.report_id,
            analysis_id=analysis_id,
            report_type=report.report_type.value,
            content_json=report.model_dump(mode="json"),
            content_markdown=markdown,
            quality_status=quality.overall_status.value,
            version=report.version,
            created_at=report.research_date,
        )
        evaluation = ReportQualityEvaluationRecord(
            id=uuid4(),
            analysis_id=analysis_id,
            report_id=report.report_id,
            metrics=quality.model_dump(mode="json"),
            evaluation_status=quality.overall_status.value,
            issues=[i.model_dump(mode="json") for i in quality.issues],
            evaluated_at=utc_now(),
        )
        async with _translate_errors(self._session):
            self._session.add(record)
            await self._session.flush()
            self._session.add(evaluation)
            await self._session.commit()
        return record.id

    async def save_usage(self, analysis_id: UUID, records: list[UsageRecord]) -> None:
        rows = [
            LLMUsageRecord(
                id=r.id,
                analysis_id=analysis_id,
                provider=r.provider,
                model=r.model,
                operation=r.operation,
                prompt_version=r.prompt_version,
                input_tokens=r.input_tokens,
                output_tokens=r.output_tokens,
                cached_tokens=r.cached_tokens,
                total_tokens=r.total_tokens,
                duration_seconds=r.duration_seconds,
                estimated_cost=r.estimated_cost,
                cost_available=r.cost_available,
                success=r.success,
                error_category=r.error_category,
                fallback_from=r.fallback_from,
                fallback_reason=r.fallback_reason,
                requested_at=r.requested_at,
            )
            for r in records
        ]
        async with _translate_errors(self._session):
            self._session.add_all(rows)
            await self._session.commit()

    # -- reads ----------------------------------------------------------------------

    async def get_report(
        self, analysis_id: UUID
    ) -> tuple[IntelligenceReport, str] | None:
        async with _translate_errors(self._session):
            row = (
                (
                    await self._session.execute(
                        select(IntelligenceReportRecord)
                        .where(IntelligenceReportRecord.analysis_id == analysis_id)
                        .order_by(IntelligenceReportRecord.version.desc())
                    )
                )
                .scalars()
                .first()
            )
        if row is None:
            return None
        return IntelligenceReport.model_validate(row.content_json), row.content_markdown

    async def get_usage(self, analysis_id: UUID) -> UsageResponse | None:
        async with _translate_errors(self._session):
            analysis = await self._session.get(IntelligenceAnalysisRecord, analysis_id)
            if analysis is None:
                return None
            rows = (
                (
                    await self._session.execute(
                        select(LLMUsageRecord)
                        .where(LLMUsageRecord.analysis_id == analysis_id)
                        .order_by(LLMUsageRecord.requested_at)
                    )
                )
                .scalars()
                .all()
            )
        entries = [
            UsageEntry(
                operation=r.operation,
                provider=r.provider,
                model=r.model,
                input_tokens=r.input_tokens,
                output_tokens=r.output_tokens,
                cached_tokens=r.cached_tokens,
                total_tokens=r.total_tokens,
                duration_seconds=r.duration_seconds,
                estimated_cost=r.estimated_cost,
                cost_available=r.cost_available,
                success=r.success,
                error_category=r.error_category,
                fallback_from=r.fallback_from,
                fallback_reason=r.fallback_reason,
                requested_at=r.requested_at,
            )
            for r in rows
        ]
        priced = [e.estimated_cost for e in entries if e.estimated_cost is not None]
        return UsageResponse(
            analysis_id=analysis_id,
            research_job_id=analysis.research_job_id,
            requests=len(entries),
            total_tokens=sum(e.total_tokens or 0 for e in entries),
            estimated_cost=round(sum(priced), 8) if priced else None,
            cost_complete=all(e.cost_available for e in entries),
            entries=entries,
        )

    async def get_quality(self, analysis_id: UUID) -> QualityResponse | None:
        async with _translate_errors(self._session):
            row = (
                (
                    await self._session.execute(
                        select(ReportQualityEvaluationRecord)
                        .where(ReportQualityEvaluationRecord.analysis_id == analysis_id)
                        .order_by(ReportQualityEvaluationRecord.evaluated_at.desc())
                    )
                )
                .scalars()
                .first()
            )
        if row is None:
            return None
        metrics = ReportQualityMetrics.model_validate(row.metrics)
        return QualityResponse(
            analysis_id=analysis_id,
            report_status=QualityStatus(row.evaluation_status),
            metrics=metrics,
            evaluated_at=row.evaluated_at,
        )

    async def count_rows(self, model: Any, analysis_id: UUID) -> int:
        """Row count for a child table (used by tests and diagnostics)."""
        async with _translate_errors(self._session):
            rows = await self._session.execute(
                select(model).where(model.analysis_id == analysis_id)
            )
            return len(rows.scalars().all())
