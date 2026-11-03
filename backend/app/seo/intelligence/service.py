"""SEO Agent Service: the execution orchestrator over the knowledge repository.

Lifecycle of one execution (used by the ``/seo-*`` Claude Code skills through
``scripts/seo_intelligence.py`` and by the API):

1. ``context``  load website config, open findings, decisions and recent history
2. ``plan``     ordered skill steps for the mode, with availability
3. ``start``    create a ``running`` execution record
4. ``record``   append what a step observed: findings, skills, tools, changes
5. ``finish``   merge into the ledger, compare with history, write the report

Nothing here contacts a website or provider; collection happens in skills or in
existing AURORA services, which hand their results to ``record``.
"""

from __future__ import annotations

from typing import Any

from app.core.exceptions import ConflictException, ResearchException
from app.seo.intelligence.history import HistoricalAuditService, apply_status
from app.seo.intelligence.knowledge import SeoKnowledgeRepository
from app.seo.intelligence.models import (
    ACTIVE_STATUSES,
    PRIORITY_ORDER,
    Execution,
    ExecutionIndexEntry,
    ExecutionMode,
    ExecutionPayload,
    ExecutionStatus,
    Finding,
    FindingStatus,
    MetricObservation,
    RejectedRecommendation,
    WebsiteConfig,
    utcnow,
)
from app.seo.intelligence.report import render_report
from app.seo.skills.registry import PlannedStep, SkillRegistry, content_hash

FINAL_STATUSES: frozenset[str] = frozenset(
    {"completed", "partial", "blocked", "failed"}
)


class SeoAgentService:
    def __init__(
        self,
        repository: SeoKnowledgeRepository,
        registry: SkillRegistry,
        history: HistoricalAuditService | None = None,
    ) -> None:
        self.repository = repository
        self.registry = registry
        self.history = history or HistoricalAuditService()

    # -- websites ----------------------------------------------------------------

    def websites(self) -> list[WebsiteConfig]:
        return [self.repository.load_config(s) for s in self.repository.list_site_ids()]

    def website(self, site_id: str) -> WebsiteConfig:
        return self.repository.load_config(site_id)

    def findings(
        self,
        site_id: str,
        *,
        status: str | None = None,
        priority: str | None = None,
        active_only: bool = False,
    ) -> list[Finding]:
        self.repository.load_config(site_id)
        items = self.repository.load_findings(site_id)
        if active_only:
            items = [f for f in items if f.status in ACTIVE_STATUSES]
        if status:
            items = [f for f in items if f.status == status]
        if priority:
            items = [f for f in items if f.priority == priority]
        return sorted(items, key=lambda f: (PRIORITY_ORDER[f.priority], f.key))

    def context(self, site_id: str, recent: int = 5) -> dict[str, Any]:
        """Everything a new execution should know before it starts."""
        config = self.repository.load_config(site_id)
        active = self.findings(site_id, active_only=True)
        index = self.repository.load_index(site_id)
        metrics = self.repository.load_metrics(site_id)
        return {
            "website": config.model_dump(mode="json"),
            "profile": self.repository.load_profile(site_id),
            "active_findings": [
                {
                    "key": f.key,
                    "priority": f.priority,
                    "status": f.status,
                    "category": f.category,
                    "issue": f.issue,
                    "last_seen": f.last_seen.isoformat() if f.last_seen else None,
                }
                for f in active
            ],
            "rejected_recommendations": [
                r.model_dump(mode="json")
                for r in self.repository.load_rejected(site_id)
            ],
            "recent_executions": [e.model_dump(mode="json") for e in index[-recent:]],
            "latest_metrics": [m.model_dump(mode="json") for m in metrics[-20:]],
            "research": [
                {"id": r.id, "kind": r.kind, "created_at": r.created_at.isoformat()}
                for r in self.repository.list_research(site_id)
            ],
        }

    # -- execution lifecycle -----------------------------------------------------

    def plan(self, site_id: str, mode: str) -> list[PlannedStep]:
        self.repository.load_config(site_id)
        return self.registry.plan(mode)

    def _new_id(self, site_id: str, mode: str) -> str:
        base = f"{utcnow():%Y%m%d-%H%M%S}-{mode}"
        existing = {e.id for e in self.repository.load_index(site_id)}
        candidate, n = base, 1
        while (
            candidate in existing
            or self.repository.execution_path(site_id, candidate).exists()
        ):
            n += 1
            candidate = f"{base}-{n}"
        return candidate

    def start(self, site_id: str, mode: ExecutionMode) -> Execution:
        self.repository.load_config(site_id)
        running = [
            e for e in self.repository.load_index(site_id) if e.status == "running"
        ]
        if running:
            raise ConflictException(
                f"Execution {running[-1].id} is still running for this website; "
                "finish it (status partial/blocked/failed) before starting another."
            )
        execution = Execution(
            id=self._new_id(site_id, mode), site_id=site_id, mode=mode
        )
        self.repository.save_execution(execution)
        self.repository.upsert_index(site_id, self._index_entry(execution))
        return execution

    def record(
        self, site_id: str, execution_id: str, payload: ExecutionPayload
    ) -> Execution:
        execution = self.repository.load_execution(site_id, execution_id)
        if execution.status != "running":
            raise ConflictException("Only a running execution can record results.")
        if payload.summary:
            execution.summary = payload.summary
        for name in ("pages_analysed", "reports_processed", "blockers", "notes"):
            merged = getattr(execution, name) + [
                v for v in getattr(payload, name) if v not in getattr(execution, name)
            ]
            setattr(execution, name, merged)
        execution.skills_used += payload.skills_used
        execution.tools_used += payload.tools_used
        execution.changes += payload.changes
        latest = {f.key: f for f in execution.findings}
        latest.update({f.key: f for f in payload.findings})
        execution.findings = list(latest.values())
        execution.checked.keys = sorted(
            set(execution.checked.keys + payload.checked.keys)
        )
        execution.checked.categories = sorted(
            set(execution.checked.categories + payload.checked.categories)
        )
        for field in (
            "learned",
            "reused",
            "patterns",
            "skill_proposals",
            "needs_approval",
        ):
            current = getattr(execution.learning, field)
            setattr(
                execution.learning,
                field,
                current
                + [v for v in getattr(payload.learning, field) if v not in current],
            )
        self._attach_hashes(execution)
        self.repository.save_execution(execution)
        return execution

    def _attach_hashes(self, execution: Execution) -> None:
        for use in execution.skills_used:
            if use.content_hash is None:
                path = self.registry.skill_file(use.name)
                if path.is_file():
                    use.content_hash = content_hash(path)

    def finish(
        self,
        site_id: str,
        execution_id: str,
        status: ExecutionStatus,
        summary: str | None = None,
    ) -> tuple[Execution, str]:
        if status not in FINAL_STATUSES:
            raise ResearchException(
                "Finish with completed, partial, blocked or failed."
            )
        config = self.repository.load_config(site_id)
        execution = self.repository.load_execution(site_id, execution_id)
        if execution.status != "running":
            raise ConflictException(f"Execution is already {execution.status}.")
        if status == "completed" and execution.blockers:
            raise ResearchException(
                "An execution with blockers cannot be 'completed'; use 'partial' or "
                "'blocked'."
            )
        if summary:
            execution.summary = summary
        ledger, comparison = self.history.merge(
            self.repository.load_findings(site_id), execution
        )
        execution.comparison = comparison
        execution.status = status
        execution.finished_at = utcnow()
        self.repository.save_findings(site_id, ledger)
        self.repository.append_changes(
            site_id, [c for c in execution.changes if c.kind == "seo_change"]
        )
        self.repository.save_execution(execution)
        self.repository.upsert_index(site_id, self._index_entry(execution))
        report = render_report(config, execution, ledger)
        self.repository.save_report(site_id, execution.id, report)
        return execution, report

    @staticmethod
    def _index_entry(execution: Execution) -> ExecutionIndexEntry:
        comparison = execution.comparison
        return ExecutionIndexEntry(
            id=execution.id,
            mode=execution.mode,
            status=execution.status,
            started_at=execution.started_at,
            finished_at=execution.finished_at,
            summary=execution.summary[:300],
            new=len(comparison.new) if comparison else 0,
            recurring=len(comparison.recurring) if comparison else 0,
            resolved=len(comparison.resolved) if comparison else 0,
        )

    # -- decisions and metrics ---------------------------------------------------

    def update_finding(
        self,
        site_id: str,
        key: str,
        status: FindingStatus,
        note: str,
        *,
        reference: str | None = None,
        execution_id: str | None = None,
    ) -> Finding:
        if status == "rejected":
            raise ResearchException("Use reject() so the reason is recorded.")
        ledger = self.repository.load_findings(site_id)
        for i, finding in enumerate(ledger):
            if finding.key == key.lower():
                ledger[i] = apply_status(
                    finding,
                    status,
                    note,
                    execution_id=execution_id,
                    reference=reference,
                )
                self.repository.save_findings(site_id, ledger)
                return ledger[i]
        raise ResearchException(f"Finding '{key}' is not in the ledger.")

    def reject(
        self, site_id: str, key: str, reason: str, decided_by: str
    ) -> RejectedRecommendation:
        if not reason.strip() or not decided_by.strip():
            raise ResearchException("A rejection needs a reason and who decided.")
        ledger = self.repository.load_findings(site_id)
        for i, finding in enumerate(ledger):
            if finding.key == key.lower():
                ledger[i] = apply_status(
                    finding, "rejected", f"{reason} (decided by {decided_by})"
                )
                record = RejectedRecommendation(
                    key=finding.key,
                    recommendation=finding.recommended_action,
                    reason=reason,
                    decided_by=decided_by,
                )
                rejected = [
                    r
                    for r in self.repository.load_rejected(site_id)
                    if r.key != finding.key
                ]
                self.repository.save_findings(site_id, ledger)
                self.repository.save_rejected(site_id, rejected + [record])
                return record
        raise ResearchException(f"Finding '{key}' is not in the ledger.")

    def record_metrics(self, site_id: str, metrics: list[MetricObservation]) -> int:
        self.repository.load_config(site_id)
        self.repository.append_metrics(site_id, metrics)
        return len(metrics)

    def executions(self, site_id: str) -> list[ExecutionIndexEntry]:
        self.repository.load_config(site_id)
        return list(reversed(self.repository.load_index(site_id)))

    def execution(self, site_id: str, execution_id: str) -> Execution:
        self.repository.load_config(site_id)
        return self.repository.load_execution(site_id, execution_id)

    def report(self, site_id: str, execution_id: str) -> str:
        self.repository.load_config(site_id)
        return self.repository.load_report(site_id, execution_id)
