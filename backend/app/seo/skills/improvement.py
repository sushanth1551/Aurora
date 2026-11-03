"""Controlled, reviewable changes to SEO skill files.

Executions may *propose* a methodology change; nothing changes a skill until a
named person approves the proposal and ``apply`` is run. Safeguards:

- Only skills whose registry ``origin`` is AURORA-owned can be edited; third-party
  skills (claude-seo, seo-toolkit, SE Ranking, ...) are reused unchanged.
- A proposal pins the skill's content hash; if the file changed since, applying
  it fails and the proposal becomes ``stale`` (re-propose against the new text).
- A proposal backed by a single execution is flagged and needs an explicit
  ``accept_single_execution`` at approval: one run is not a pattern.
- ``replace_text`` must match exactly once; every applied change is appended to
  ``skills/CHANGELOG.md`` with before/after hashes, so git shows the full diff.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from app.core.exceptions import (
    ConflictException,
    ResearchException,
    ResourceNotFoundException,
)
from app.seo.intelligence.knowledge import write_atomic
from app.seo.intelligence.models import utcnow
from app.seo.skills.registry import SkillRegistry, content_hash

EDITABLE_ORIGINS = frozenset({"aurora-seo-intelligence", "aurora-planning"})
PROPOSAL_ID_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
ProposalStatus = Literal["pending", "approved", "rejected", "applied", "stale"]


class SkillChange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["append_section", "replace_text"]
    text: str = Field(min_length=1, max_length=20_000)
    section_title: str | None = None
    old_text: str | None = None


class SkillProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    skill: str
    rationale: str = Field(min_length=10)
    evidence: list[str] = Field(min_length=1)
    evidence_executions: list[str] = []
    change: SkillChange
    base_hash: str
    created_at: datetime = Field(default_factory=utcnow)
    created_by: str = "seo-intelligence-agent"
    status: ProposalStatus = "pending"
    single_execution: bool = False
    decided_by: str | None = None
    decided_at: datetime | None = None
    decision_note: str = ""
    applied_at: datetime | None = None
    new_hash: str | None = None


class SkillImprovementService:
    def __init__(self, registry: SkillRegistry, knowledge_root: Path) -> None:
        self.registry = registry
        self.folder = Path(knowledge_root) / "skills" / "proposals"
        self.changelog = Path(knowledge_root) / "skills" / "CHANGELOG.md"
        self.baseline = Path(knowledge_root) / "skills" / "skill-hashes.json"

    def _path(self, proposal_id: str) -> Path:
        if not PROPOSAL_ID_RE.match(proposal_id) or len(proposal_id) > 100:
            raise ResourceNotFoundException("Skill proposal not found.")
        return self.folder / f"{proposal_id}.json"

    def _save(self, proposal: SkillProposal) -> None:
        write_atomic(self._path(proposal.id), proposal.model_dump_json(indent=2) + "\n")

    def get(self, proposal_id: str) -> SkillProposal:
        path = self._path(proposal_id)
        if not path.exists():
            raise ResourceNotFoundException("Skill proposal not found.")
        return TypeAdapter(SkillProposal).validate_json(path.read_bytes())

    def list(self, status: str | None = None) -> list[SkillProposal]:
        if not self.folder.is_dir():
            return []
        items = [self.get(p.stem) for p in sorted(self.folder.glob("*.json"))]
        return [p for p in items if status is None or p.status == status]

    def _origin(self, skill: str) -> str:
        for entry in self.registry.load().skills:
            if entry.name == skill:
                return entry.origin
        raise ResourceNotFoundException(f"Skill '{skill}' is not in the registry.")

    def propose(
        self,
        skill: str,
        change: SkillChange,
        rationale: str,
        evidence: list[str],
        evidence_executions: list[str],
    ) -> SkillProposal:
        origin = self._origin(skill)
        if origin not in EDITABLE_ORIGINS:
            raise ResearchException(
                f"'{skill}' is a third-party skill ({origin}); propose the change in an "
                "AURORA-owned wrapper skill instead of editing it."
            )
        path = self.registry.skill_file(skill)
        if not path.is_file():
            raise ResourceNotFoundException(f"Skill '{skill}' is not installed.")
        if change.kind == "replace_text" and not change.old_text:
            raise ResearchException("replace_text proposals need old_text.")
        for pending in self.list("pending"):
            if pending.skill == skill and pending.change == change:
                raise ConflictException(f"Proposal {pending.id} already covers this.")
        stamp = utcnow().strftime("%Y%m%d%H%M%S")
        proposal = SkillProposal(
            id=f"{stamp}-{skill}",
            skill=skill,
            rationale=rationale,
            evidence=evidence,
            evidence_executions=sorted(set(evidence_executions)),
            change=change,
            base_hash=content_hash(path),
            single_execution=len(set(evidence_executions)) < 2,
        )
        self._save(proposal)
        return proposal

    def decide(
        self,
        proposal_id: str,
        *,
        approve: bool,
        decided_by: str,
        note: str = "",
        accept_single_execution: bool = False,
    ) -> SkillProposal:
        proposal = self.get(proposal_id)
        if proposal.status != "pending":
            raise ConflictException(f"Proposal is already {proposal.status}.")
        if not decided_by.strip():
            raise ResearchException("A decision needs the name of the person deciding.")
        if approve and proposal.single_execution and not accept_single_execution:
            raise ResearchException(
                "This proposal rests on a single execution; approve it only with an "
                "explicit accept_single_execution."
            )
        proposal.status = "approved" if approve else "rejected"
        proposal.decided_by = decided_by.strip()
        proposal.decided_at = utcnow()
        proposal.decision_note = note
        self._save(proposal)
        return proposal

    def apply(self, proposal_id: str) -> SkillProposal:
        proposal = self.get(proposal_id)
        if proposal.status != "approved":
            raise ConflictException("Only approved proposals can be applied.")
        path = self.registry.skill_file(proposal.skill)
        current = content_hash(path)
        if current != proposal.base_hash:
            proposal.status = "stale"
            self._save(proposal)
            raise ConflictException(
                "The skill changed after this proposal was made; it is now stale."
            )
        text = path.read_text(encoding="utf-8").replace("\r\n", "\n")
        change = proposal.change
        if change.kind == "replace_text":
            assert change.old_text is not None
            if text.count(change.old_text) != 1:
                raise ConflictException(
                    "old_text must occur exactly once in the skill."
                )
            text = text.replace(change.old_text, change.text)
        else:
            title = change.section_title or "Learned refinement"
            text = text.rstrip("\n") + f"\n\n## {title}\n\n{change.text.strip()}\n"
        write_atomic(path, text)
        proposal.status = "applied"
        proposal.applied_at = utcnow()
        proposal.new_hash = content_hash(path)
        self._save(proposal)
        self._log(proposal, current)
        return proposal

    def _log(self, proposal: SkillProposal, before: str) -> None:
        header = "# SEO skill change log\n\nApplied, approved skill proposals.\n"
        existing = (
            self.changelog.read_text(encoding="utf-8")
            if self.changelog.exists()
            else header
        )
        entry = (
            f"\n## {proposal.applied_at:%Y-%m-%d %H:%M} UTC · `{proposal.skill}`\n\n"
            f"- Proposal: `{proposal.id}` ({proposal.change.kind})\n"
            f"- Approved by: {proposal.decided_by}\n"
            f"- Rationale: {proposal.rationale}\n"
            f"- Evidence executions: {', '.join(proposal.evidence_executions) or 'none'}\n"
            f"- Hash: `{before[:12]}` → `{(proposal.new_hash or '')[:12]}`\n"
        )
        write_atomic(self.changelog, existing.rstrip("\n") + "\n" + entry)

    def detect_direct_edits(self) -> list[str]:
        """Registered skills whose files changed since the last check.

        Changes made through ``apply`` are already logged; anything else (an owner
        editing a SKILL.md by hand) is logged here as a direct edit and becomes the
        new baseline, so later executions record and use the current version. The
        first call only creates the baseline.
        """
        current = {
            entry.name: content_hash(self.registry.skill_file(entry.name))
            for entry in self.registry.load().skills
            if self.registry.skill_file(entry.name).is_file()
        }
        first = not self.baseline.exists()
        previous: dict[str, str] = (
            {} if first else json.loads(self.baseline.read_text(encoding="utf-8"))
        )
        applied = {p.new_hash for p in self.list("applied") if p.new_hash}
        edited = sorted(
            name
            for name, digest in current.items()
            if not first
            and name in previous
            and previous[name] != digest
            and digest not in applied
        )
        write_atomic(
            self.baseline, json.dumps(current, indent=2, sort_keys=True) + "\n"
        )
        if edited:
            header = "# SEO skill change log\n\nApplied, approved skill proposals.\n"
            text = (
                self.changelog.read_text(encoding="utf-8")
                if self.changelog.exists()
                else header
            )
            stamp = utcnow().strftime("%Y-%m-%d %H:%M")
            for name in edited:
                text = text.rstrip("\n") + (
                    f"\n\n## {stamp} UTC · `{name}` (direct edit detected)\n\n"
                    "- Changed outside the proposal process (by the owner or by hand).\n"
                    f"- Hash: `{previous[name][:12]}` → `{current[name][:12]}`\n"
                )
            write_atomic(self.changelog, text)
        return edited
