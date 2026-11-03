"""Deterministic Markdown report for one SEO execution (no LLM involved).

Every section is rendered from the stored execution record and the site's
finding ledger, so a report can always be regenerated and never says more than
the record does.
"""

from __future__ import annotations

from collections.abc import Iterable

from app.seo.intelligence.models import (
    PRIORITY_ORDER,
    Execution,
    Finding,
    WebsiteConfig,
)

DISCLAIMER = (
    "This report does not predict rankings or traffic. Expected outcomes describe "
    "what each change is meant to improve; verify them with the stated method."
)
STATUS_TEXT = {
    "completed": "Completed: every planned step ran with the data it needed.",
    "partial": "Partially completed: some steps ran without all their data (see blockers).",
    "blocked": "Blocked: required data, credentials or permissions were missing.",
    "failed": "Failed: the execution stopped on an error (see notes).",
    "running": "Still running: this report is provisional.",
}


def _cell(text: object) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ").strip() or "–"


def _bullets(items: Iterable[str], empty: str = "None.") -> str:
    lines = [f"- {item}" for item in items]
    return "\n".join(lines) if lines else empty


def _finding_block(f: Finding) -> str:
    urls = ", ".join(f.affected_urls[:10]) or "site-wide"
    if len(f.affected_urls) > 10:
        urls += f" (+{len(f.affected_urls) - 10} more)"
    evidence = "; ".join(
        f"{e.source}: {e.observation}" + (f" ({e.url})" if e.url else "")
        for e in f.evidence[-3:]
    )
    return (
        f"#### {f.issue}\n\n"
        f"- **Key:** `{f.key}` · **Category:** {f.category} · "
        f"**Evidence state:** {f.evidence_state}\n"
        f"- **Evidence:** {evidence}\n"
        f"- **Affected URL(s):** {urls}\n"
        f"- **SEO impact:** {f.seo_impact}\n"
        f"- **Priority:** {f.priority.capitalize()}\n"
        f"- **Recommended action:** {f.recommended_action}\n"
        f"- **Implementation status:** {f.status.replace('_', ' ')}\n"
        f"- **Expected outcome:** {f.expected_outcome}\n"
        f"- **Verification method:** {f.verification_method}\n"
    )


def render_report(
    config: WebsiteConfig, execution: Execution, ledger: list[Finding]
) -> str:
    comparison = execution.comparison
    by_key = {f.key: f for f in ledger}
    current = [by_key.get(f.key, f) for f in execution.findings]
    current.sort(key=lambda f: (PRIORITY_ORDER[f.priority], f.key))
    started = execution.started_at.strftime("%Y-%m-%d %H:%M UTC")
    finished = (
        execution.finished_at.strftime("%Y-%m-%d %H:%M UTC")
        if execution.finished_at
        else "not finished"
    )

    out: list[str] = [
        f"# SEO execution report: {config.name} ({execution.mode})",
        "",
        f"> {DISCLAIMER}",
        "",
        "## Execution summary",
        "",
        f"- **Website:** {config.name} ({config.base_url})",
        f"- **Execution:** `{execution.id}` · started {started} · finished {finished}",
        f"- **Status:** {STATUS_TEXT[execution.status]}",
        f"- **Pages analysed:** {len(execution.pages_analysed)}",
        f"- **Reports processed:** {len(execution.reports_processed)}",
    ]
    if execution.summary:
        out += ["", execution.summary]
    if execution.reports_processed:
        out += ["", "Reports processed:", _bullets(execution.reports_processed)]
    if execution.pages_analysed:
        shown = execution.pages_analysed[:25]
        more = len(execution.pages_analysed) - len(shown)
        out += ["", "Pages analysed:", _bullets(shown)]
        if more > 0:
            out.append(f"- … and {more} more (see the execution record)")
    out += ["", "### Main findings", ""]
    out.append(
        _bullets(
            (f"**{f.priority.capitalize()}**: {f.issue}" for f in current[:7]),
            "No findings were recorded in this execution.",
        )
    )
    out += ["", "### Compared with previous executions", ""]
    if comparison is None:
        out.append("No comparison was made (execution not finished).")
    else:
        out += [
            "| Change | Count | Finding keys |",
            "| --- | --- | --- |",
            f"| New | {len(comparison.new)} | {_cell(', '.join(comparison.new))} |",
            f"| Recurring | {len(comparison.recurring)} | "
            f"{_cell(', '.join(comparison.recurring))} |",
            f"| Reopened | {len(comparison.reopened)} | "
            f"{_cell(', '.join(comparison.reopened))} |",
            f"| Resolved | {len(comparison.resolved)} | "
            f"{_cell(', '.join(comparison.resolved))} |",
            f"| Not rechecked (still open) | {len(comparison.not_rechecked)} | "
            f"{_cell(', '.join(comparison.not_rechecked[:40]))} |",
        ]
        stale = [
            f.key
            for f in ledger
            if f.status == "implemented" and f.key in set(comparison.recurring)
        ]
        if stale:
            out += [
                "",
                "Marked implemented but still observed (check deployment): "
                + ", ".join(f"`{k}`" for k in stale),
            ]

    for section in execution.sections:
        out += ["", f"## {section.title}", "", section.markdown.strip()]

    out += ["", "## Skills used", ""]
    if execution.skills_used:
        out += [
            "| Skill | Why selected | What it analysed | Actions | Output | Version |",
            "| --- | --- | --- | --- | --- | --- |",
        ]
        for s in execution.skills_used:
            out.append(
                f"| `{s.name}` | {_cell(s.reason)} | {_cell(s.analysed)} | "
                f"{_cell('; '.join(s.actions))} | {_cell(s.output)} | "
                f"{(s.content_hash or 'unknown')[:12]} |"
            )
    else:
        out.append("No skills were recorded.")

    out += ["", "## Tools and integrations used", ""]
    if execution.tools_used:
        out += [
            "| Tool / source | Kind | Status | Calls | Note |",
            "| --- | --- | --- | --- | --- |",
        ]
        for t in execution.tools_used:
            out.append(
                f"| {_cell(t.name)} | {t.kind} | {t.status} | {t.calls} | "
                f"{_cell(t.note)} |"
            )
        missing = [
            t for t in execution.tools_used if t.status in {"unavailable", "failed"}
        ]
        if missing:
            out += [
                "",
                "Unavailable or failed: "
                + "; ".join(
                    f"{t.name} ({t.status}: {t.note or 'no detail'})" for t in missing
                ),
            ]
    else:
        out.append("No tools were recorded.")

    out += ["", "## Changes performed", ""]
    groups = {
        "file_created": "Files created",
        "file_modified": "Files modified",
        "seo_change": "SEO changes implemented",
        "knowledge": "Knowledge records created",
        "skill": "Skill files updated",
    }
    for kind, title in groups.items():
        items = [c for c in execution.changes if c.kind == kind]
        lines = [
            (f"`{c.path}`: " if c.path else "")
            + c.description
            + (f" (ref {c.reference})" if c.reference else "")
            + (f", approved by {c.approved_by}" if c.approved_by else "")
            for c in items
        ]
        out += [f"**{title}**", "", _bullets(lines), ""]

    out += ["## Recommendations", ""]
    labels = {
        "critical": "Critical improvements",
        "high": "High-priority improvements",
        "medium": "Medium-priority improvements",
        "low": "Future opportunities (low priority)",
    }
    for level, title in labels.items():
        items = [f for f in current if f.priority == level]
        out += [f"### {title}", ""]
        out += [_finding_block(f) for f in items] or ["None in this execution.", ""]

    learning = execution.learning
    out += [
        "## Learning summary",
        "",
        "**What this execution learned**",
        "",
        _bullets(learning.learned),
        "",
        "**Historical information reused**",
        "",
        _bullets(learning.reused),
        "",
        "**Patterns identified**",
        "",
        _bullets(learning.patterns),
        "",
        "**Proposed skill improvements** (not applied; see `/seo-update-skills`)",
        "",
        _bullets(learning.skill_proposals),
        "",
        "**Requires human approval**",
        "",
        _bullets(learning.needs_approval),
        "",
        "## Blockers and notes",
        "",
        _bullets(execution.blockers + execution.notes),
        "",
    ]
    return "\n".join(out)
