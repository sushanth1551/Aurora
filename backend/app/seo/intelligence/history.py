"""Historical audit service: merge an execution's findings into the site ledger.

Comparison rules (deterministic, documented in ``docs/seo-intelligence.md``):

- observed now, never seen before                      -> **new**
- observed now, seen before and still active           -> **recurring**
- observed now, previously resolved or verified        -> **reopened** (status open)
- observed now, previously rejected or deferred        -> recurring; the human
  decision is kept, never overridden by a later execution
- not observed, and its key or category was rechecked  -> **resolved**
  (``verified`` when it had been marked implemented)
- not observed, and not rechecked                      -> **not_rechecked**;
  absence of evidence is not evidence of a fix
"""

from __future__ import annotations

from datetime import datetime

from app.seo.intelligence.models import (
    ACTIVE_STATUSES,
    CheckedScope,
    Comparison,
    Execution,
    Finding,
    FindingStatus,
    StatusEvent,
    utcnow,
)

MAX_EVIDENCE_PER_FINDING = 20


def _rechecked(finding: Finding, checked: CheckedScope) -> bool:
    keys = {k.lower() for k in checked.keys}
    categories = {c.lower() for c in checked.categories}
    return finding.key in keys or finding.category.lower() in categories


def _event(status: FindingStatus, note: str, execution_id: str) -> StatusEvent:
    return StatusEvent(status=status, note=note, execution_id=execution_id)


class HistoricalAuditService:
    """Pure merge/compare logic; persistence stays in the repository."""

    def merge(
        self, ledger: list[Finding], execution: Execution
    ) -> tuple[list[Finding], Comparison]:
        now = utcnow()
        by_key = {f.key: f.model_copy(deep=True) for f in ledger}
        comparison = Comparison()
        observed: set[str] = set()

        for current in execution.findings:
            if current.key in observed:
                continue
            observed.add(current.key)
            for item in current.evidence:
                if item.execution_id is None:
                    item.execution_id = execution.id
                if item.observed_at is None:
                    item.observed_at = now
            previous = by_key.get(current.key)
            if previous is None:
                created = current.model_copy(deep=True)
                created.first_seen = created.first_seen or now
                created.last_seen = now
                created.seen_in = [execution.id]
                created.history = [
                    _event(created.status, "first observed", execution.id)
                ]
                by_key[created.key] = created
                comparison.new.append(created.key)
                continue
            by_key[current.key] = self._update(previous, current, execution.id, now)
            if previous.status in {"resolved", "verified"}:
                comparison.reopened.append(current.key)
            else:
                comparison.recurring.append(current.key)

        for key, finding in by_key.items():
            if key in observed or finding.status not in ACTIVE_STATUSES:
                continue
            if _rechecked(finding, execution.checked):
                status: FindingStatus = (
                    "verified" if finding.status == "implemented" else "resolved"
                )
                finding.status = status
                finding.history.append(
                    _event(status, "rechecked and not observed", execution.id)
                )
                comparison.resolved.append(key)
            else:
                comparison.not_rechecked.append(key)

        for bucket in (
            comparison.new,
            comparison.recurring,
            comparison.resolved,
            comparison.not_rechecked,
            comparison.reopened,
        ):
            bucket.sort()
        return list(by_key.values()), comparison

    @staticmethod
    def _update(
        previous: Finding, current: Finding, execution_id: str, now: datetime
    ) -> Finding:
        merged = previous.model_copy(deep=True)
        for field in (
            "issue",
            "category",
            "priority",
            "evidence_state",
            "seo_impact",
            "recommended_action",
            "expected_outcome",
            "verification_method",
        ):
            setattr(merged, field, getattr(current, field))
        if current.affected_urls:
            merged.affected_urls = current.affected_urls
        merged.skill = current.skill or previous.skill
        merged.evidence = (previous.evidence + current.evidence)[
            -MAX_EVIDENCE_PER_FINDING:
        ]
        merged.last_seen = now
        if execution_id not in merged.seen_in:
            merged.seen_in.append(execution_id)
        if previous.status in {"resolved", "verified"}:
            merged.status = "open"
            merged.history.append(_event("open", "observed again", execution_id))
        elif previous.status == "implemented":
            merged.history.append(
                _event(
                    "implemented",
                    "still observed after being marked implemented; "
                    "check deployment and re-verify",
                    execution_id,
                )
            )
        return merged


def apply_status(
    finding: Finding,
    status: FindingStatus,
    note: str,
    *,
    execution_id: str | None = None,
    reference: str | None = None,
) -> Finding:
    """Record a human or skill decision on one finding (with an auditable event)."""
    updated = finding.model_copy(deep=True)
    updated.status = status
    updated.history.append(
        StatusEvent(
            status=status, note=note, execution_id=execution_id, reference=reference
        )
    )
    return updated
