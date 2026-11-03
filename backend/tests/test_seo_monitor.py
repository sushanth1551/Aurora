"""SEO monitor: change tracking from git, checklist, rankings, learning, skill edits.

Uses a throw-away git repository and injected Search Console/live fetchers.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from datetime import date, timedelta
from pathlib import Path

import pytest

from app.seo.intelligence import rankings
from app.seo.intelligence.changes import ChangeTracker, GitError, GitReader, diff_posts
from app.seo.intelligence.knowledge import SeoKnowledgeRepository
from app.seo.intelligence.models import (
    Integrations,
    RepositoryLink,
    WebsiteConfig,
)
from app.seo.intelligence.monitor import (
    CHECKLIST_FILE,
    ChecklistItem,
    MonitorService,
    live_title,
    title_matches,
)
from app.seo.intelligence.service import SeoAgentService
from app.seo.skills.improvement import SkillImprovementService
from app.seo.skills.registry import SkillRegistry

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")

TODAY = date(2026, 11, 3)
POSTS = "frontend/src/data/manual-posts.json"


def git(repo: Path, *args: str, when: str | None = None) -> None:
    env = {
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@t",
        "PATH": __import__("os").environ["PATH"],
        "SYSTEMROOT": __import__("os").environ.get("SYSTEMROOT", ""),
    }
    if when:
        env["GIT_AUTHOR_DATE"] = env["GIT_COMMITTER_DATE"] = f"{when}T10:00:00+00:00"
    subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, env=env
    )


def write_posts(repo: Path, posts: list[dict]) -> None:
    path = repo / POSTS
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(posts), encoding="utf-8")


@pytest.fixture
def site(tmp_path: Path):
    repo = tmp_path / "site"
    repo.mkdir()
    git(repo, "init", "-b", "main")
    write_posts(
        repo, [{"slug": "a", "title": "Old A", "excerpt": "x", "content": "one two"}]
    )
    git(repo, "add", ".")
    git(repo, "commit", "-m", "initial", when="2026-08-01")
    write_posts(
        repo,
        [
            {
                "slug": "a",
                "title": "New A title",
                "excerpt": "x",
                "content": "one two three four",
                "faq": [{"q": 1}],
            },
            {"slug": "b", "title": "B", "excerpt": "y", "content": "z"},
        ],
    )
    git(repo, "commit", "-am", "content(blog): retitle a, add b", when="2026-09-01")
    # A remote-tracking ref so "on production" can be tested without a network.
    git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    git(repo, "checkout", "-b", "feature")
    (repo / "frontend" / "nginx.conf").write_text("x", encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "config only (not tracked)", when="2026-10-20")
    write_posts(
        repo,
        [
            {
                "slug": "a",
                "title": "Newest A",
                "excerpt": "x",
                "content": "one two three four",
                "faq": [{"q": 1}],
            },
            {"slug": "b", "title": "B", "excerpt": "y", "content": "z"},
        ],
    )
    git(repo, "commit", "-am", "content(blog): retitle a again", when="2026-10-25")

    root = tmp_path / "kb"
    skills = tmp_path / "skills"
    (skills / "seo-monitor").mkdir(parents=True)
    (skills / "seo-monitor" / "SKILL.md").write_text(
        "---\nname: seo-monitor\n---\nv1\n"
    )
    (root / "skills").mkdir(parents=True)
    (root / "skills" / "skill-registry.json").write_text(
        json.dumps(
            {
                "version": 1,
                "updated": "x",
                "skills": [
                    {
                        "name": "seo-monitor",
                        "role": "r",
                        "origin": "aurora-seo-intelligence",
                    }
                ],
                "modes": {},
            }
        )
    )
    kb = SeoKnowledgeRepository(root)
    config = WebsiteConfig(
        id="demo",
        name="Demo",
        domain="demo.com",
        base_url="https://demo.com",
        integrations=Integrations(search_console_property="sc-domain:demo.com"),
        repository=RepositoryLink(path=str(repo), seo_paths={"manual_posts": POSTS}),
    )
    kb.save_config(config)
    registry = SkillRegistry(root / "skills" / "skill-registry.json", skills)
    agent = SeoAgentService(kb, registry)
    return {
        "repo": repo,
        "kb": kb,
        "config": config,
        "agent": agent,
        "improvement": SkillImprovementService(registry, root),
        "skills": skills,
    }


def gsc_rows(days: int = 150, improve_after: date = date(2026, 9, 1)):
    rows = []
    for n in range(days):
        day = TODAY - timedelta(days=3 + n)
        better = day > improve_after
        rows.append(
            {
                "keys": [day.isoformat(), "https://demo.com/blog/a"],
                "clicks": 5 if better else 2,
                "impressions": 100,
                "ctr": 0.05,
                "position": 4.0 if better else 9.0,
            }
        )
        rows.append(
            {
                "keys": [day.isoformat(), "https://demo.com/blog/b/"],
                "clicks": 1,
                "impressions": 40,
                "ctr": 0.02,
                "position": 12.0 if day > TODAY - timedelta(days=17) else 7.0,
            }
        )
    return rows


# --- pure helpers ------------------------------------------------------------------


def test_diff_posts_reports_field_changes() -> None:
    edits = diff_posts(
        json.dumps([{"slug": "a", "title": "T1", "content": "a b"}]),
        json.dumps(
            [
                {"slug": "a", "title": "T2", "content": "a b c", "faq": [1, 2]},
                {"slug": "n", "title": "New"},
            ]
        ),
    )
    assert edits["a"].new_title == "T2"
    assert "title: 'T1' -> 'T2'" in edits["a"].changes
    assert "faq changed (2 questions)" in edits["a"].changes
    assert "body updated (+1 words, now 3)" in edits["a"].changes
    assert edits["n"].changes == ["post added"]
    assert diff_posts("not json", None) == {}


def test_git_reader_refuses_write_commands(tmp_path: Path) -> None:
    with pytest.raises(GitError):
        GitReader(tmp_path).run("push")
    with pytest.raises(GitError):
        GitReader(tmp_path).run("commit", "-m", "x")


def test_rankings_math_and_verdicts() -> None:
    rows = rankings.to_rows(gsc_rows())
    assert {r.path for r in rows} == {"/blog/a", "/blog/b"}
    stats = rankings.window(rows, TODAY - timedelta(days=16), TODAY - timedelta(days=3))
    assert stats["/blog/a"].position == pytest.approx(4.0)
    better = rankings.Stats(clicks=30, impressions=500, weighted_position=4.0 * 500)
    worse = rankings.Stats(clicks=10, impressions=500, weighted_position=9.0 * 500)
    assert rankings.verdict(worse, better) == "improved"
    assert rankings.verdict(better, worse) == "declined"
    assert rankings.verdict(None, better) == "newly ranking"
    assert (
        rankings.verdict(rankings.Stats(), rankings.Stats(impressions=5))
        == "not enough data"
    )


def test_relative_verdict_discounts_site_wide_growth() -> None:
    def stats(clicks: float, impressions: float, position: float) -> rankings.Stats:
        return rankings.Stats(clicks, impressions, position * impressions)

    page_before, page_after = stats(10, 500, 9.0), stats(20, 800, 7.0)
    # The whole blog improved just as much: no credit to the change.
    assert (
        rankings.relative_verdict(
            page_before, page_after, stats(100, 5000, 9.0), stats(200, 8000, 7.0)
        )
        == "in line with site"
    )
    # The blog stayed flat: the page beat the trend.
    assert (
        rankings.relative_verdict(
            page_before, page_after, stats(100, 5000, 8.0), stats(100, 5000, 8.0)
        )
        == "beat site trend"
    )
    assert (
        rankings.relative_verdict(
            page_after, page_before, stats(100, 5000, 8.0), stats(100, 5000, 8.0)
        )
        == "behind site trend"
    )


def test_title_helpers() -> None:
    assert live_title("<html><title> New &amp; A — Brand</title>") == "New & A — Brand"
    assert title_matches("New & A — Brand", "New & A")
    assert not title_matches("Old", "New")
    assert not title_matches(None, "New")


# --- monitor run ------------------------------------------------------------------


async def test_monitor_tracks_changes_checklist_rankings_and_learning(site) -> None:
    calls: list[tuple[date, date]] = []

    async def gsc(start: date, end: date):
        calls.append((start, end))
        return gsc_rows()

    async def live(url: str):
        return "<title>Newest A — Demo</title>" if url.endswith("/blog/a") else None

    monitor = MonitorService(
        site["agent"],
        site["improvement"],
        tracker=ChangeTracker(site["config"]),
        gsc=gsc,
        live=live,
        today=TODAY,
    )
    execution = await monitor.run("demo")

    assert execution.status == "completed"
    assert calls and calls[0][1] == TODAY - timedelta(days=3)
    commits = json.loads(
        (site["kb"].site_dir("demo") / "changes/commits.json").read_text()
    )
    subjects = [c["subject"] for c in commits]
    assert "config only (not tracked)" not in subjects  # not an SEO path
    on_prod = {c["subject"]: c["deployed"] for c in commits}
    assert on_prod["content(blog): retitle a, add b"] is True
    assert on_prod["content(blog): retitle a again"] is True  # proved by the live title

    items = site["kb"].load_list("demo", CHECKLIST_FILE, ChecklistItem)
    by_id = {i.id: i for i in items}
    older_live = [i for i in items if i.kind == "live-title" and i.path == "/blog/a"]
    assert sorted(i.status for i in older_live) == ["done", "skipped"]
    rank = next(
        i
        for i in items
        if i.kind == "rank"
        and i.days == 28
        and i.path == "/blog/a"
        and i.status == "done"
    )
    assert rank.result.startswith("beat site trend")
    assert any(
        i.kind == "reindex"
        and i.status == "skipped"
        and i.result == "change predates monitoring"
        for i in items
    )
    assert len({i.id for i in items}) == len(by_id)

    effects = json.loads(
        (site["kb"].site_dir("demo") / "learning/change-effects.json").read_text()
    )
    assert effects["effects"]["title"]["28d"]["beat site trend"] >= 1

    report = site["kb"].load_report("demo", execution.id)
    for heading in (
        "## Blog rankings (Search Console)",
        "## Change checklist",
        "## Your changes since the last run",
        "## What your changes did (learning)",
    ):
        assert heading in report
    drop = next(f for f in execution.findings if f.key == "ranking-drop:/blog/b")
    assert drop.priority == "medium" and "7.0 to 12.0" in drop.issue

    # Second run: nothing new, nothing duplicated; the skill edit is detected.
    (site["skills"] / "seo-monitor" / "SKILL.md").write_text(
        "---\nname: seo-monitor\n---\nv2\n"
    )
    again = await MonitorService(
        site["agent"],
        site["improvement"],
        tracker=ChangeTracker(site["config"]),
        gsc=gsc,
        live=live,
        today=TODAY + timedelta(days=14),
    ).run("demo")
    assert len(site["kb"].load_list("demo", CHECKLIST_FILE, ChecklistItem)) == len(
        items
    )
    assert "Skill edits detected: seo-monitor" in again.learning.learned
    changelog = site["kb"].root / "skills" / "CHANGELOG.md"
    assert "direct edit detected" in changelog.read_text(encoding="utf-8")


async def test_monitor_without_sources_is_honest(site) -> None:
    execution = await MonitorService(
        site["agent"], site["improvement"], today=TODAY
    ).run("demo")
    assert execution.status == "blocked"
    assert any("Search Console" in b for b in execution.blockers)

    async def failing(start: date, end: date):
        raise RuntimeError("quota")

    partial = await MonitorService(
        site["agent"],
        site["improvement"],
        tracker=ChangeTracker(site["config"]),
        gsc=failing,
        today=TODAY,
    ).run("demo")
    assert partial.status == "partial"
    assert any(t.status == "failed" for t in partial.tools_used)


# --- PDF report ---------------------------------------------------------------------


def test_markdown_to_html_and_pdf_names(tmp_path: Path) -> None:
    from app.seo.intelligence.models import SchedulerSettings
    from app.seo.intelligence.pdf_report import markdown_to_html, pdf_path

    page = markdown_to_html(
        "## Rankings\n\n| Page | Clicks |\n| --- | --- |\n| /blog/a <x> | 5 |\n\n"
        "- [ ] **Deploy** `3c54aa5`\n> note",
        "Next Step Scheduler Report 2026-11-17",
    )
    assert "<h2>Rankings</h2>" in page and "<td>/blog/a &lt;x&gt;</td>" in page
    assert "☐ <strong>Deploy</strong> <code>3c54aa5</code>" in page
    assert "<blockquote>note</blockquote>" in page

    first = pdf_path(tmp_path, "Next Step Scheduler Report {date}", date(2026, 11, 17))
    assert first.name == "Next Step Scheduler Report 2026-11-17.pdf"
    first.write_bytes(b"x")
    second = pdf_path(tmp_path, "Next Step Scheduler Report {date}", date(2026, 11, 17))
    assert second.name == "Next Step Scheduler Report 2026-11-17 (2).pdf"

    assert SchedulerSettings().run_time == "12:00"
    with pytest.raises(ValueError):
        SchedulerSettings(run_time="25:00")
    with pytest.raises(ValueError):
        SchedulerSettings(pdf_name="no date here")
    with pytest.raises(ValueError):
        SchedulerSettings(pdf_name="../evil {date}")
