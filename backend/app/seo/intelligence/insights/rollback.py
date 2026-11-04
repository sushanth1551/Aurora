"""Rollback of a recorded SEO change in the website repository (approval-gated).

``plan`` is read-only: it finds the recorded change (by commit reference or
finding key), shows the commit, its files and the diff that a revert would undo.

``prepare`` needs the owner's name as approval and a clean working tree. It
creates a new local branch ``seo-rollback/<sha>`` from the current branch and
runs ``git revert --no-commit <sha>``, so the undo is staged for review. It never
commits, pushes, merges, resets or deploys; the owner reviews the staged diff
and commits it (or runs ``git revert --abort`` and deletes the branch).
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from app.seo.intelligence.models import ChangeRecord

SHA_RE = re.compile(r"^[0-9a-f]{7,40}$")


class RollbackError(RuntimeError):
    pass


def _git(repo: Path, *args: str, check: bool = True) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )
    if check and result.returncode != 0:
        raise RollbackError(
            f"git {' '.join(args[:2])} failed: {result.stderr.strip()[:300]}"
        )
    return result.stdout


@dataclass
class RollbackPlan:
    sha: str
    description: str
    subject: str
    files: list[str]
    diff_stat: str
    contained_in: list[str]
    current_branch: str


def find_change(changes: list[ChangeRecord], ref: str) -> ChangeRecord:
    ref = ref.strip()
    for c in changes:
        if c.reference and (c.reference.startswith(ref) or ref.startswith(c.reference)):
            return c
    for c in changes:
        if ref in c.finding_keys:
            return c
    raise RollbackError(
        f"No recorded change matches '{ref}' (commit reference or finding key)."
    )


def plan(repo: Path, changes: list[ChangeRecord], ref: str) -> RollbackPlan:
    change = find_change(changes, ref)
    sha = (change.reference or "").strip()
    if not SHA_RE.match(sha):
        raise RollbackError(f"The recorded change has no commit reference ('{sha}').")
    full = _git(repo, "rev-parse", "--verify", f"{sha}^{{commit}}").strip()
    subject = _git(repo, "show", "-s", "--format=%s", full).strip()
    files = [
        f
        for f in _git(repo, "show", "--name-only", "--format=", full).splitlines()
        if f
    ]
    stat = _git(repo, "show", "--stat", "--format=", full).strip()
    branches = [
        b.strip().lstrip("* ").strip()
        for b in _git(repo, "branch", "-a", "--contains", full).splitlines()
        if b.strip()
    ]
    current = _git(repo, "branch", "--show-current").strip()
    return RollbackPlan(
        sha=full,
        description=change.description,
        subject=subject,
        files=files,
        diff_stat=stat,
        contained_in=branches[:20],
        current_branch=current,
    )


def prepare(repo: Path, rollback: RollbackPlan, approved_by: str) -> tuple[str, str]:
    """Stage the revert on a new branch. Returns (branch name, staged diff stat)."""
    if not approved_by.strip():
        raise RollbackError("Approval is required: pass the owner's name.")
    if _git(repo, "status", "--porcelain").strip():
        raise RollbackError(
            "The working tree has uncommitted changes; commit or stash them first."
        )
    if rollback.current_branch in ("main", "master"):
        raise RollbackError(
            "Refusing to prepare a rollback from the production branch; check out a work branch first."
        )
    branch = f"seo-rollback/{rollback.sha[:10]}"
    if _git(repo, "branch", "--list", branch).strip():
        raise RollbackError(
            f"Branch {branch} already exists; review or delete it first."
        )
    _git(repo, "switch", "-c", branch)
    result = subprocess.run(
        ["git", "-C", str(repo), "revert", "--no-commit", rollback.sha],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )
    if result.returncode != 0:
        raise RollbackError(
            "git revert reported conflicts; resolve them on "
            f"{branch} or run 'git revert --abort': {result.stderr.strip()[:300]}"
        )
    return branch, _git(repo, "diff", "--cached", "--stat").strip()
