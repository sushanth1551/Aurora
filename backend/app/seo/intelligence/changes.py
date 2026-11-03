"""Detect SEO-relevant changes the owner made in a website's repository (read-only git).

Each commit that touches a configured SEO path becomes a ``TrackedCommit`` with
per-page edits ("title: 'A' -> 'B'", "body updated (+420 words)") and whether it
is already on the production branch. Only read-only git commands are run
(``log``, ``show``, ``merge-base --is-ancestor``); nothing is fetched or written.
"""

from __future__ import annotations

import json
import re
import subprocess
from datetime import date, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

from app.seo.intelligence.models import WebsiteConfig

GIT_TIMEOUT_SECONDS = 30
MAX_COMMITS_PER_RUN = 200
POST_FIELDS = ("title", "excerpt", "faq")
TAG_RE = re.compile(r"<[^>]+>")
SEP = "\x1f"
READ_ONLY_GIT = frozenset({"log", "show", "merge-base", "rev-parse"})


class PageEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str
    changes: list[str]
    new_title: str | None = None


class TrackedCommit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sha: str
    committed_at: datetime
    subject: str
    files: list[str]
    pages: list[PageEdit] = []
    deployed: bool = False
    #: Best known go-live date and how it was established (never guessed silently).
    deploy_date: date | None = None
    deploy_basis: str = ""


class GitError(RuntimeError):
    pass


class GitReader:
    """Runs a small allow-list of read-only git commands in one repository."""

    def __init__(self, repo: Path) -> None:
        self.repo = Path(repo)

    def run(self, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        if not args or args[0] not in READ_ONLY_GIT:
            raise GitError(f"git {args[:1]} is not an allowed read-only command")
        try:
            result = subprocess.run(
                ["git", "-C", str(self.repo), *args],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=GIT_TIMEOUT_SECONDS,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise GitError(f"git {args[0]} failed: {type(exc).__name__}") from exc
        if check and result.returncode != 0:
            raise GitError(f"git {args[0]} failed: {result.stderr.strip()[:200]}")
        return result

    def show_file(self, rev: str, path: str) -> str | None:
        result = self.run("show", f"{rev}:{path}", check=False)
        return result.stdout if result.returncode == 0 else None

    def is_ancestor(self, sha: str, ref: str) -> bool:
        return (
            self.run("merge-base", "--is-ancestor", sha, ref, check=False).returncode
            == 0
        )


def _words(text: str) -> int:
    return len(TAG_RE.sub(" ", text).split())


def _posts(raw: str | None) -> dict[str, dict[str, Any]]:
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except ValueError:
        return {}
    items = data if isinstance(data, list) else []
    return {str(p["slug"]): p for p in items if isinstance(p, dict) and p.get("slug")}


def _short(value: object, limit: int = 90) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def diff_posts(before: str | None, after: str | None) -> dict[str, PageEdit]:
    """Per-slug edits between two versions of a posts JSON list."""
    old, new = _posts(before), _posts(after)
    edits: dict[str, PageEdit] = {}
    for slug in sorted(set(old) | set(new)):
        path = f"/blog/{slug}"
        if slug not in old:
            edits[slug] = PageEdit(
                path=path, changes=["post added"], new_title=new[slug].get("title")
            )
            continue
        if slug not in new:
            edits[slug] = PageEdit(path=path, changes=["post removed from this file"])
            continue
        changes: list[str] = []
        for field in POST_FIELDS:
            a, b = old[slug].get(field), new[slug].get(field)
            if a == b:
                continue
            if field == "faq":
                count = len(b) if isinstance(b, list) else 0
                changes.append(f"faq changed ({count} questions)")
            else:
                changes.append(f"{field}: '{_short(a)}' -> '{_short(b)}'")
        a_body, b_body = old[slug].get("content"), new[slug].get("content")
        if isinstance(a_body, str) and isinstance(b_body, str) and a_body != b_body:
            delta = _words(b_body) - _words(a_body)
            changes.append(f"body updated ({delta:+d} words, now {_words(b_body)})")
        if changes:
            title = new[slug].get("title")
            edits[slug] = PageEdit(
                path=path,
                changes=changes,
                new_title=title
                if any(c.startswith("title") for c in changes)
                else None,
            )
    return edits


class ChangeTracker:
    """Turns repository commits into tracked SEO changes for one website."""

    def __init__(self, config: WebsiteConfig, git: GitReader | None = None) -> None:
        if not config.repository.path:
            raise GitError("The website config has no repository path.")
        self.config = config
        self.git = git or GitReader(Path(config.repository.path))
        paths = config.repository.seo_paths
        self.post_files = [
            v for k, v in paths.items() if k in {"manual_posts", "blog_index_data"}
        ]
        self.body_dir = paths.get("post_bodies")
        self.tracked = sorted(set(paths.values()))
        self.production = f"origin/{config.repository.production_branch}"

    def _is_seo_path(self, path: str) -> bool:
        return any(
            path == p or (p.endswith("/") and path.startswith(p)) for p in self.tracked
        )

    def commits_since(self, since: date) -> list[TrackedCommit]:
        result = self.git.run(
            "log",
            "--branches",
            f"--remotes=origin/{self.config.repository.production_branch}",
            f"--since={since.isoformat()}",
            f"--max-count={MAX_COMMITS_PER_RUN}",
            f"--format={SEP}%H{SEP}%cI{SEP}%s",
            "--name-only",
        )
        commits: list[TrackedCommit] = []
        for block in result.stdout.split(f"\n{SEP}"):
            block = block.lstrip(SEP)
            if not block.strip():
                continue
            header, _, rest = block.partition("\n")
            parts = header.split(SEP)
            if len(parts) != 3:
                continue
            sha, when, subject = parts
            files = [f for f in rest.splitlines() if f.strip()]
            seo_files = [f for f in files if self._is_seo_path(f)]
            if not seo_files:
                continue
            commits.append(
                TrackedCommit(
                    sha=sha,
                    committed_at=datetime.fromisoformat(when),
                    subject=subject[:200],
                    files=seo_files[:100],
                    pages=self.page_edits(sha, seo_files),
                )
            )
        return sorted(commits, key=lambda c: c.committed_at)

    def page_edits(self, sha: str, files: list[str]) -> list[PageEdit]:
        merged: dict[str, PageEdit] = {}
        for path in files:
            if path in self.post_files:
                edits = diff_posts(
                    self.git.show_file(f"{sha}^", path), self.git.show_file(sha, path)
                )
                for slug, edit in edits.items():
                    if slug in merged:
                        known = merged[slug]
                        known.changes += [
                            c for c in edit.changes if c not in known.changes
                        ]
                        known.new_title = known.new_title or edit.new_title
                    else:
                        merged[slug] = edit
            elif (
                self.body_dir
                and path.startswith(self.body_dir)
                and path.endswith(".json")
            ):
                slug = Path(path).stem
                note = "body file updated"
                if slug in merged:
                    if note not in merged[slug].changes:
                        merged[slug].changes.append(note)
                else:
                    merged[slug] = PageEdit(path=f"/blog/{slug}", changes=[note])
        site_files = [
            f
            for f in files
            if f not in self.post_files
            and not (self.body_dir and f.startswith(self.body_dir))
        ]
        edits = list(merged.values())
        if site_files:
            edits.append(
                PageEdit(
                    path="site",
                    changes=[f"site file changed: {f}" for f in site_files[:10]],
                )
            )
        return edits

    def refresh_deployment(
        self, commits: list[TrackedCommit], today: date, *, first_sync: bool
    ) -> None:
        """Mark commits on the production branch (as last fetched locally).

        CI deploys the production branch on push, so a commit already there at the
        first sync is dated by its commit date (approximate); one that arrives later
        is dated by the run that first saw it (at most one monitor interval late).
        """
        for commit in commits:
            if commit.deployed or not self.git.is_ancestor(commit.sha, self.production):
                continue
            commit.deployed = True
            if first_sync:
                commit.deploy_date = commit.committed_at.date()
                commit.deploy_basis = "commit date (already on production branch)"
            else:
                commit.deploy_date = today
                commit.deploy_basis = (
                    "first monitor run that saw it on the production branch"
                )
