"""Scheduled monitoring: detect the owner's changes, keep a checklist, report rankings.

One ``run`` (mode ``monitor``, meant for every 14 days):

1. Sync SEO commits from the website repository (``changes.ChangeTracker``).
2. Create checklist items per changed page: deployed, live title check, reindex
   requested, ranking checks 14/28/56 days after go-live, re-verify linked findings.
   Only the newest live-title/reindex item per page stays open (older ones are
   superseded); changes older than 30 days at the first sync skip manual items.
3. Evaluate due items automatically (git, a bounded number of live fetches, the
   site's reindex tracker, Search Console rows).
4. Learn which kinds of change helped: ranking verdicts are aggregated by change
   type into ``learning/change-effects.json``; strong, repeated effects are
   reported as candidate rules (never applied automatically).
5. Report blog rankings (last 14 days vs previous 14) and ranking drops as findings.
6. Detect direct edits to skill files and log them.
7. Record a ``monitor`` execution through ``SeoAgentService`` (report + history).

Search Console and live fetches are injected callables, so tests need no network.
"""

from __future__ import annotations

import html
import json
import re
from collections import Counter, defaultdict
from collections.abc import Awaitable, Callable
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.seo.intelligence import rankings
from app.seo.intelligence.changes import (
    ChangeTracker,
    GitError,
    PageEdit,
    TrackedCommit,
)
from app.seo.intelligence.knowledge import write_atomic
from app.seo.intelligence.models import (
    ACTIVE_STATUSES,
    CheckedScope,
    Evidence,
    Execution,
    ExecutionPayload,
    Finding,
    LearningSummary,
    ReportSection,
    SkillUse,
    ToolUse,
    utcnow,
)
from app.seo.intelligence.service import SeoAgentService
from app.seo.skills.improvement import SkillImprovementService

COMMITS_FILE = "changes/commits.json"
CHECKLIST_FILE = "checklist.json"
EFFECTS_FILE = "learning/change-effects.json"
FIRST_SYNC_DAYS = 90
MANUAL_ITEM_MAX_AGE_DAYS = 30
GSC_LAG_DAYS = 3
RANK_CHECK_DAYS = (14, 28, 56)
MAX_LIVE_CHECKS = 15
CANDIDATE_MIN_SAMPLES = 5
CANDIDATE_MIN_SHARE = 0.7
DATE_RE = re.compile(r"\b(20\d\d-\d\d-\d\d)\b")
TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)

GscFetcher = Callable[[date, date], Awaitable[list[dict[str, object]]]]
LiveFetcher = Callable[[str], Awaitable[str | None]]
ItemKind = Literal["deploy", "live-title", "reindex", "rank", "verify-finding"]


class ChecklistItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    commit: str
    path: str
    kind: ItemKind
    title: str
    due: date | None = None
    status: Literal["pending", "done", "skipped"] = "pending"
    result: str = ""
    days: int | None = None
    expected: str | None = None
    change_types: list[str] = []
    finding_key: str | None = None
    updated_at: datetime | None = None


def live_title(page_html: str) -> str | None:
    match = TITLE_RE.search(page_html)
    return " ".join(html.unescape(match.group(1)).split()) if match else None


def title_matches(live: str | None, expected: str) -> bool:
    if not live or not expected:
        return False

    def norm(text: str) -> str:
        return " ".join(html.unescape(text).split()).lower()

    return norm(live).startswith(norm(expected)) or norm(expected) in norm(live)


def change_types(edit: PageEdit) -> list[str]:
    """Classify a page edit: title, description, body, faq, new-post."""
    kinds: set[str] = set()
    for change in edit.changes:
        if change.startswith("title"):
            kinds.add("title")
        elif change.startswith("excerpt"):
            kinds.add("description")
        elif change.startswith("faq"):
            kinds.add("faq")
        elif change.startswith("body"):
            kinds.add("body")
        elif change == "post added":
            kinds.add("new-post")
    return sorted(kinds) or ["other"]


def verdict_of(result: str) -> str:
    return result.split(":", 1)[0] if ":" in result else result


class MonitorService:
    def __init__(
        self,
        agent: SeoAgentService,
        improvement: SkillImprovementService,
        *,
        tracker: ChangeTracker | None = None,
        gsc: GscFetcher | None = None,
        live: LiveFetcher | None = None,
        today: date | None = None,
    ) -> None:
        self.agent = agent
        self.repo = agent.repository
        self.improvement = improvement
        self.tracker = tracker
        self.gsc = gsc
        self.live = live
        self.today = today or utcnow().date()

    # -- changes -----------------------------------------------------------------

    def sync_changes(
        self, site_id: str
    ) -> tuple[list[TrackedCommit], list[TrackedCommit], bool]:
        assert self.tracker is not None
        known = self.repo.load_list(site_id, COMMITS_FILE, TrackedCommit)
        first_sync = not known
        since = (
            self.today - timedelta(days=FIRST_SYNC_DAYS)
            if first_sync
            else max(c.committed_at for c in known).date() - timedelta(days=1)
        )
        seen = {c.sha for c in known}
        new = [c for c in self.tracker.commits_since(since) if c.sha not in seen]
        commits = known + new
        self.tracker.refresh_deployment(commits, self.today, first_sync=first_sync)
        self.repo.save_list(site_id, COMMITS_FILE, commits)
        return commits, new, first_sync

    # -- checklist ---------------------------------------------------------------

    def build_items(
        self, commit: TrackedCommit, findings: list[Finding], *, manual: bool = True
    ) -> list[ChecklistItem]:
        short = commit.sha[:7]
        items = [
            ChecklistItem(
                id=f"{short}:deploy",
                commit=commit.sha,
                path="site",
                kind="deploy",
                title=f"Deploy '{commit.subject}'",
                due=commit.committed_at.date(),
            )
        ]
        skipped = "skipped" if not manual else "pending"
        for edit in commit.pages:
            if edit.path == "site":
                continue
            types = change_types(edit)
            if edit.new_title:
                items.append(
                    ChecklistItem(
                        id=f"{short}:live-title:{edit.path}",
                        commit=commit.sha,
                        path=edit.path,
                        kind="live-title",
                        title="Live page shows the new title",
                        expected=edit.new_title,
                        change_types=types,
                    )
                )
            items.append(
                ChecklistItem(
                    id=f"{short}:reindex:{edit.path}",
                    commit=commit.sha,
                    path=edit.path,
                    kind="reindex",
                    title="Request indexing in Search Console after deploy (manual)",
                    status=skipped,
                    result="" if manual else "change predates monitoring",
                    change_types=types,
                )
            )
            for days in RANK_CHECK_DAYS:
                items.append(
                    ChecklistItem(
                        id=f"{short}:rank-{days}d:{edit.path}",
                        commit=commit.sha,
                        path=edit.path,
                        kind="rank",
                        days=days,
                        title=f"Ranking {days} days after go-live vs {days} days before",
                        change_types=types,
                    )
                )
            for finding in findings:
                if (
                    manual
                    and finding.status in ACTIVE_STATUSES
                    and any(
                        rankings.page_path(u) == edit.path
                        for u in finding.affected_urls
                    )
                ):
                    items.append(
                        ChecklistItem(
                            id=f"verify:{finding.key}",
                            commit=commit.sha,
                            path=edit.path,
                            kind="verify-finding",
                            finding_key=finding.key,
                            title=f"Re-verify '{finding.key}' in the next /seo-audit",
                        )
                    )
        return items

    @staticmethod
    def supersede(
        items: list[ChecklistItem], commits: dict[str, TrackedCommit]
    ) -> None:
        """Keep only the newest pending live-title/reindex item per page."""
        latest: dict[tuple[str, str], ChecklistItem] = {}
        for item in items:
            if item.kind not in {"live-title", "reindex"} or item.commit not in commits:
                continue
            key = (item.kind, item.path)
            current = latest.get(key)
            if current is None or (
                commits[item.commit].committed_at > commits[current.commit].committed_at
            ):
                latest[key] = item
        for item in items:
            if item.kind in {"live-title", "reindex"} and item.status == "pending":
                newest = latest.get((item.kind, item.path))
                if newest is not None and newest is not item:
                    item.status = "skipped"
                    item.result = f"superseded by {newest.commit[:7]}"
        # Commits to one page that went live on the same day share one measurement.
        ranks: dict[tuple[str, int, date], ChecklistItem] = {}
        for item in items:
            commit = commits.get(item.commit)
            if item.kind != "rank" or item.status == "skipped" or not commit:
                continue
            if commit.deploy_date is None or item.days is None:
                continue
            key = (item.path, item.days, commit.deploy_date)
            keep = ranks.get(key)
            if keep is None:
                ranks[key] = item
                continue
            keep.change_types = sorted(set(keep.change_types) | set(item.change_types))
            item.status = "skipped"
            item.result = f"same go-live date as {keep.commit[:7]}"

    def _tracker_lines(self, site_id: str) -> list[str]:
        config = self.agent.website(site_id)
        rel = config.repository.seo_paths.get("reindex_tracker")
        if not rel or not config.repository.path:
            return []
        try:
            return (
                (Path(config.repository.path) / rel)
                .read_text(encoding="utf-8", errors="replace")
                .splitlines()
            )
        except OSError:
            return []

    async def evaluate(
        self,
        site_id: str,
        items: list[ChecklistItem],
        commits: dict[str, TrackedCommit],
        rows: list[rankings.DayRow] | None,
        data_end: date,
    ) -> int:
        """Update pending items in place; returns the number of live fetches made."""
        tracker = self._tracker_lines(site_id)
        findings = {f.key: f for f in self.repo.load_findings(site_id)}
        base = self.agent.website(site_id).base_url.rstrip("/")
        live_calls = 0
        now = utcnow()
        for item in items:
            if item.status != "pending":
                continue
            commit = commits.get(item.commit)
            if commit is None:
                continue
            before_result = (item.status, item.result)
            if item.kind == "deploy" and commit.deployed:
                item.status = "done"
                item.result = f"on production ({commit.deploy_basis})"
            elif (
                item.kind == "live-title" and self.live and live_calls < MAX_LIVE_CHECKS
            ):
                live_calls += 1
                page = await self.live(base + item.path)
                seen = live_title(page) if page else None
                if title_matches(seen, item.expected or ""):
                    item.status = "done"
                    item.result = f"live title confirmed on {self.today}"
                    if not commit.deployed:
                        commit.deployed = True
                        commit.deploy_date = self.today
                        commit.deploy_basis = "live title confirmed by monitor"
                else:
                    item.result = (
                        f"live still shows '{seen or 'no title / not fetched'}'"
                    )
            elif item.kind == "reindex":
                slug = item.path.rsplit("/", 1)[-1]
                since = commit.committed_at.date().isoformat()
                if any(
                    slug in line
                    and "requested" in line.lower()
                    and any(d >= since for d in DATE_RE.findall(line))
                    for line in tracker
                ):
                    item.status, item.result = "done", "marked requested in the tracker"
            elif item.kind == "rank" and commit.deploy_date and rows is not None:
                assert item.days is not None
                due = commit.deploy_date + timedelta(days=item.days)
                item.due = due
                if due <= data_end:
                    pre = rankings.window(
                        rows,
                        commit.deploy_date - timedelta(days=item.days),
                        commit.deploy_date - timedelta(days=1),
                    )
                    post = rankings.window(
                        rows, commit.deploy_date + timedelta(days=1), due
                    )
                    before, after = pre.get(item.path), post.get(item.path)
                    site_before, site_after = rankings.total(pre), rankings.total(post)
                    verdict = rankings.relative_verdict(
                        before, after, site_before, site_after
                    )
                    item.status = "done"
                    item.result = (
                        f"{verdict}: {rankings.describe(before, after)}; all blog "
                        f"pages: position {rankings.fmt_pos(site_before.position)} -> "
                        f"{rankings.fmt_pos(site_after.position)}, clicks "
                        f"{site_before.clicks:.0f} -> {site_after.clicks:.0f}"
                    )
            elif item.kind == "verify-finding" and item.finding_key:
                finding = findings.get(item.finding_key)
                if finding is None or finding.status not in ACTIVE_STATUSES:
                    item.status = "done"
                    item.result = f"finding is {finding.status if finding else 'gone'}"
            if (item.status, item.result) != before_result:
                item.updated_at = now
        return live_calls

    # -- learning ----------------------------------------------------------------

    def change_effects(self, site_id: str, items: list[ChecklistItem]) -> dict:
        """Aggregate ranking verdicts by change type and window; saved for reuse."""
        table: dict[str, dict[str, Counter[str]]] = defaultdict(
            lambda: defaultdict(Counter)
        )
        for item in items:
            if item.kind != "rank" or item.status != "done" or item.days is None:
                continue
            for kind in item.change_types or ["other"]:
                table[kind][f"{item.days}d"][verdict_of(item.result)] += 1
        effects = {
            kind: {window: dict(counts) for window, counts in windows.items()}
            for kind, windows in sorted(table.items())
        }
        data = {
            "computed_on": self.today.isoformat(),
            "method": (
                "Search Console page position/clicks in the N days after go-live vs the N "
                "days before, relative to all blog pages over the same windows; verdict "
                "rules in app/seo/intelligence/rankings.py (relative_verdict). Other "
                "changes, seasonality and Google updates also move rankings: these are "
                "observations, not proof of cause."
            ),
            "effects": effects,
        }
        write_atomic(
            self.repo.site_dir(site_id) / EFFECTS_FILE,
            json.dumps(data, indent=2) + "\n",
        )
        return effects

    @staticmethod
    def effects_markdown(effects: dict) -> tuple[str, list[str]]:
        verdicts = [
            "beat site trend",
            "in line with site",
            "behind site trend",
            "newly ranking",
            "not enough data",
        ]
        lines = [
            "Pages changed in your commits: ranking N days after go-live vs N days "
            "before, compared with all blog pages over the same days (site-wide growth "
            "or a Google update is not credited to the change). Pages edited several "
            "times count once per go-live date. Observations, not proof of cause.",
            "",
            "| Change type | Window | " + " | ".join(verdicts) + " |",
            "| --- | --- | " + " | ".join("---" for _ in verdicts) + " |",
        ]
        candidates: list[str] = []
        for kind, windows in effects.items():
            for window_name in sorted(windows, key=lambda w: int(w[:-1])):
                counts = windows[window_name]
                lines.append(
                    f"| {kind} | {window_name} | "
                    + " | ".join(str(counts.get(v, 0)) for v in verdicts)
                    + " |"
                )
                decided = counts.get("beat site trend", 0) + counts.get(
                    "behind site trend", 0
                )
                # One window decides candidates, so a rule is not counted three times.
                if window_name == "28d" and decided >= CANDIDATE_MIN_SAMPLES:
                    for direction in ("beat site trend", "behind site trend"):
                        share = counts.get(direction, 0) / decided
                        if share >= CANDIDATE_MIN_SHARE:
                            candidates.append(
                                f"'{kind}' changes {direction} at {window_name} in "
                                f"{counts[direction]} of {decided} measured pages"
                            )
        return "\n".join(lines), candidates

    # -- run ---------------------------------------------------------------------

    async def run(self, site_id: str) -> Execution:
        config = self.agent.website(site_id)
        execution = self.agent.start(site_id, "monitor")
        base = config.base_url.rstrip("/")
        blockers: list[str] = []
        tools: list[ToolUse] = []
        sections: list[ReportSection] = []
        learning = LearningSummary()
        findings: list[Finding] = []
        checked = CheckedScope()

        commits: list[TrackedCommit] = []
        new: list[TrackedCommit] = []
        first_sync = False
        if self.tracker is None:
            blockers.append("No repository configured: changes were not tracked.")
        else:
            try:
                commits, new, first_sync = self.sync_changes(site_id)
                tools.append(
                    ToolUse(
                        name="git (read-only) on the site repository",
                        kind="git",
                        status="success",
                        calls=1 + len(new),
                    )
                )
            except GitError as exc:
                blockers.append(f"Repository changes not read: {exc}")
                tools.append(
                    ToolUse(
                        name="git (read-only)",
                        kind="git",
                        status="failed",
                        note=str(exc)[:200],
                    )
                )
        by_sha = {c.sha: c for c in commits}

        items = self.repo.load_list(site_id, CHECKLIST_FILE, ChecklistItem)
        existing = {i.id for i in items}
        ledger = self.repo.load_findings(site_id)
        cutoff = self.today - timedelta(days=MANUAL_ITEM_MAX_AGE_DAYS)
        for commit in new:
            manual = not (first_sync and commit.committed_at.date() < cutoff)
            for item in self.build_items(commit, ledger, manual=manual):
                if item.id not in existing:
                    existing.add(item.id)
                    items.append(item)
        self.supersede(items, by_sha)

        data_end = self.today - timedelta(days=GSC_LAG_DAYS)
        rows: list[rankings.DayRow] | None = None
        if self.gsc is None:
            blockers.append("Search Console not available: rankings not measured.")
            tools.append(
                ToolUse(
                    name="Google Search Console API",
                    kind="api",
                    status="unavailable",
                    note="not configured for this run",
                )
            )
        else:
            starts = [
                c.deploy_date - timedelta(days=max(RANK_CHECK_DAYS))
                for c in commits
                if c.deploy_date
            ]
            start = max(
                min([data_end - timedelta(days=27), *starts]),
                data_end - timedelta(days=180),
            )
            try:
                rows = rankings.to_rows(await self.gsc(start, data_end))
                tools.append(
                    ToolUse(
                        name="Google Search Console API (date x page)",
                        kind="api",
                        status="success",
                        calls=1,
                        note=f"{start}..{data_end}, {len(rows)} rows",
                    )
                )
            except Exception as exc:  # provider/DB errors become a recorded blocker
                blockers.append(f"Search Console request failed: {type(exc).__name__}")
                tools.append(
                    ToolUse(
                        name="Google Search Console API",
                        kind="api",
                        status="failed",
                        note=str(exc)[:200],
                    )
                )

        live_calls = await self.evaluate(site_id, items, by_sha, rows, data_end)
        if self.live is not None:
            tools.append(
                ToolUse(
                    name="Live page fetch (UrlPolicy-validated)",
                    kind="other",
                    status="success",
                    calls=live_calls,
                )
            )
        self.repo.save_list(site_id, COMMITS_FILE, commits)
        self.repo.save_list(site_id, CHECKLIST_FILE, items)

        # Rankings and ranking-drop findings.
        if rows is not None:
            p = rankings.periods(data_end)
            md, movers = rankings.ranking_table(rows, p)
            sections.append(
                ReportSection(title="Blog rankings (Search Console)", markdown=md)
            )
            checked = CheckedScope(categories=["performance/ranking"])
            for m in movers:
                before, after = m["before"], m["after"]
                if m["position_change"] < 3 or after.impressions < 100:
                    continue
                url = base + m["path"]
                findings.append(
                    Finding(
                        key=f"ranking-drop:{m['path']}",
                        issue=(
                            f"{m['path']}: average position fell from "
                            f"{rankings.fmt_pos(before.position)} to "
                            f"{rankings.fmt_pos(after.position)}"
                        ),
                        category="performance/ranking",
                        priority="high" if before.clicks >= 20 else "medium",
                        evidence=[
                            Evidence(
                                source=f"Search Console {p.previous[0]}..{p.current[1]}",
                                observation=rankings.describe(before, after),
                                url=url,
                            )
                        ],
                        affected_urls=[url],
                        seo_impact="Fewer impressions at visible positions; clicks usually follow.",
                        recommended_action=(
                            "Check recent edits to this page in the change checklist, its "
                            "query mix in Search Console and the pages now ranking above it; "
                            "revert a recent title change if it caused the drop."
                        ),
                        expected_outcome="The cause is identified before more changes are made.",
                        verification_method=(
                            "Next monitor run: position back within 1 of the earlier window."
                        ),
                        skill="seo-toolkit-performance-analysis",
                    )
                )

        # Changes made since the last run.
        if new:
            lines: list[str] = []
            for c in new[-40:]:
                state = "live" if c.deployed else "NOT on production yet"
                lines.append(
                    f"- `{c.sha[:7]}` {c.committed_at:%Y-%m-%d} {c.subject} ({state})"
                )
                for edit in c.pages[:8]:
                    lines.append(f"  - {edit.path}: " + "; ".join(edit.changes[:3]))
            if len(new) > 40:
                lines.insert(0, f"Showing the newest 40 of {len(new)} commits.\n")
            sections.append(
                ReportSection(
                    title="Your changes since the last run", markdown="\n".join(lines)
                )
            )
            kinds = Counter(
                t
                for c in new
                for e in c.pages
                if e.path != "site"
                for t in change_types(e)
            )
            learning.patterns.append(
                "Change types this period: "
                + ", ".join(f"{k} x{v}" for k, v in sorted(kinds.items()))
            )
        undeployed = [c for c in commits if not c.deployed]
        if undeployed:
            learning.needs_approval += [
                f"Deploy {c.sha[:7]} ({c.subject})" for c in undeployed[:10]
            ]

        # Checklist: what needs you, what was measured, what is next.
        pending = [i for i in items if i.status == "pending"]

        def live(item: ChecklistItem) -> bool:
            commit = by_sha.get(item.commit)
            return bool(commit and commit.deployed)

        manual = [
            i
            for i in pending
            if i.kind in {"deploy", "verify-finding"}
            or (i.kind == "reindex" and live(i))
        ]
        waiting = Counter(
            i.commit[:7] for i in pending if i.kind == "reindex" and not live(i)
        )
        live_open = [
            i for i in pending if i.kind == "live-title" and i.result and live(i)
        ]
        measured_now = [
            i
            for i in items
            if i.kind == "rank"
            and i.status == "done"
            and i.updated_at
            and i.updated_at.date() == self.today
        ]
        upcoming = sorted(
            (i for i in pending if i.kind == "rank" and i.due),
            key=lambda i: (i.due, i.path),
        )
        md = [
            f"{len(pending)} open item(s). Full checklist: "
            f"`seo-intelligence/sites/{site_id}/{CHECKLIST_FILE}`.",
            "",
            "**Needs you**",
            "",
        ]
        md += [
            f"- [ ] {i.title} · {i.path} · `{i.commit[:7]}`"
            + (f" · {i.result}" if i.result else "")
            for i in manual[:30]
        ] or ["- Nothing."]
        if len(manual) > 30:
            md.append(f"- … and {len(manual) - 30} more")
        if waiting:
            md.append(
                "- After deploying: request indexing for "
                + ", ".join(f"{n} page(s) of `{sha}`" for sha, n in waiting.items())
                + " (these reminders appear once the change is live)."
            )
        md += ["", "**Deployed, but the live page does not show the new title**", ""]
        md += [f"- {i.path}: {i.result}" for i in live_open[:20]] or ["- None."]
        md += ["", "**Ranking checks completed in this run**", ""]
        md += [
            f"- {i.path} (`{i.commit[:7]}`, {'/'.join(i.change_types)}, {i.days}d): {i.result}"
            for i in measured_now[:30]
        ] or ["- None due."]
        if len(measured_now) > 30:
            md.append(f"- … and {len(measured_now) - 30} more (see the checklist file)")
        md += ["", "**Next ranking checks**", ""]
        md += [f"- {i.due}: {i.path} ({i.days}d)" for i in upcoming[:15]] or [
            "- None scheduled (waiting for changes to go live)."
        ]
        sections.append(ReportSection(title="Change checklist", markdown="\n".join(md)))

        # What the site's own history says about kinds of change.
        effects = self.change_effects(site_id, items)
        if effects:
            effects_md, candidates = self.effects_markdown(effects)
            if candidates:
                effects_md += (
                    "\n\n**Candidate rules** (need your approval before any skill "
                    "or config changes):\n\n" + "\n".join(f"- {c}" for c in candidates)
                )
                learning.skill_proposals += [f"Candidate rule: {c}" for c in candidates]
            sections.append(
                ReportSection(
                    title="What your changes did (learning)", markdown=effects_md
                )
            )
            totals = Counter(
                verdict_of(i.result)
                for i in items
                if i.kind == "rank" and i.status == "done"
            )
            learning.learned.append(
                "Measured page changes so far: "
                + ", ".join(f"{k} {v}" for k, v in sorted(totals.items()))
            )
        learning.reused.append(
            f"{len(commits)} tracked commits and {len(items)} checklist items"
        )

        # Skill files edited directly.
        edits = self.improvement.detect_direct_edits()
        if edits:
            sections.append(
                ReportSection(
                    title="Skill files changed directly",
                    markdown="\n".join(
                        f"- `{e}`: changed outside the proposal process; logged in "
                        "`seo-intelligence/skills/CHANGELOG.md`; later runs use this version."
                        for e in edits
                    ),
                )
            )
            learning.learned.append("Skill edits detected: " + ", ".join(edits))

        payload = ExecutionPayload(
            summary=(
                f"Scheduled monitor: {len(new)} new change commit(s), {len(manual)} item(s) "
                f"need you, {len(measured_now)} ranking check(s) completed, rankings "
                f"{'measured' if rows is not None else 'not measured'}."
            ),
            skills_used=[
                SkillUse(
                    name="seo-monitor",
                    reason="scheduled monitor mode",
                    analysed="repository changes, checklist, Search Console rows",
                    actions=[
                        "synced changes",
                        "updated checklist",
                        "compared rankings",
                    ],
                    output=f"{len(sections)} report sections",
                ),
                SkillUse(
                    name="seo-toolkit-performance-analysis",
                    reason="method for period comparison and ranking drops",
                    analysed="blog pages, 14-day windows",
                    output=f"{len(findings)} ranking findings",
                ),
            ],
            tools_used=tools,
            findings=findings,
            checked=checked,
            blockers=blockers,
            sections=sections,
            learning=learning,
        )
        self.agent.record(site_id, execution.id, payload)
        status = (
            "completed"
            if not blockers
            else ("blocked" if rows is None and not commits else "partial")
        )
        finished, _ = self.agent.finish(site_id, execution.id, status)
        return finished
