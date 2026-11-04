"""Read-only skill registry for the SEO Intelligence layer.

The registry joins a reviewed mapping (``<knowledge root>/skills/skill-registry.json``:
which skill plays which role in which execution mode) with the skill files on
disk (``.claude/skills/<name>/SKILL.md``). It reports each skill's frontmatter
version and a SHA-256 content hash, so every execution can record exactly which
methodology text it followed.

Skills are guidance text. This module never executes, imports or injects them
into a prompt, and a skill can never add a tool, URL, provider call or write
(Module 6 design §2.4). Changing a skill goes through ``improvement.py``.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, TypeAdapter, ValidationError

from app.core.exceptions import ConfigurationException, ResourceNotFoundException

Availability = Literal["available", "missing_skill", "missing_setting", "runtime_check"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Requirement(_Strict):
    """``setting`` is a Settings field name; ``mcp`` can only be checked at run time."""

    kind: Literal["setting", "mcp", "env", "file"]
    name: str
    note: str = ""


class SkillEntry(_Strict):
    name: str
    role: str
    origin: str
    modes: list[str] = []
    requires: list[Requirement] = []
    replaces_request: str | None = None
    historical_use: str = ""


class ModeStep(_Strict):
    key: str
    skill: str
    purpose: str
    optional: bool = False
    when: str = ""
    # Steps that share a group are independent and may run at the same time
    # (parallel subagents); results are still recorded one after another.
    group: str = ""


class ModeDefinition(_Strict):
    entry_skill: str
    description: str
    steps: list[ModeStep]


class RegistryFile(_Strict):
    version: int
    updated: str
    skills: list[SkillEntry]
    modes: dict[str, ModeDefinition]


class SkillInfo(BaseModel):
    name: str
    role: str
    origin: str
    modes: list[str]
    exists: bool
    description: str | None = None
    version: str | None = None
    content_hash: str | None = None
    historical_use: str = ""


class PlannedStep(BaseModel):
    key: str
    skill: str
    purpose: str
    optional: bool
    when: str
    group: str = ""
    availability: Availability
    reasons: list[str] = []
    content_hash: str | None = None


def content_hash(path: Path) -> str:
    """SHA-256 of the file with normalised line endings (stable across OSes)."""
    data = path.read_bytes().replace(b"\r\n", b"\n")
    return hashlib.sha256(data).hexdigest()


def read_frontmatter(path: Path) -> dict[str, object]:
    text = path.read_text(encoding="utf-8").replace("\r\n", "\n")
    if not text.startswith("---\n"):
        return {}
    end = text.find("\n---", 4)
    if end == -1:
        return {}
    try:
        data = yaml.safe_load(text[4:end])
    except yaml.YAMLError:
        return {}
    return data if isinstance(data, dict) else {}


def _version_of(front: dict[str, object]) -> str | None:
    meta = front.get("metadata")
    if isinstance(meta, dict) and meta.get("version") is not None:
        return str(meta["version"])
    version = front.get("version")
    return str(version) if version is not None else None


class SkillRegistry:
    def __init__(
        self,
        registry_path: Path,
        skills_dir: Path,
        configured_settings: set[str] | None = None,
    ) -> None:
        self.registry_path = Path(registry_path)
        self.skills_dir = Path(skills_dir)
        self._configured = configured_settings or set()

    def load(self) -> RegistryFile:
        if not self.registry_path.exists():
            raise ConfigurationException("The SEO skill registry file is missing.")
        try:
            return TypeAdapter(RegistryFile).validate_json(
                self.registry_path.read_bytes()
            )
        except ValidationError as exc:
            raise ConfigurationException(
                f"The SEO skill registry is invalid ({exc.error_count()} errors)."
            ) from exc

    def skill_file(self, name: str) -> Path:
        return self.skills_dir / name / "SKILL.md"

    def describe(self, entry: SkillEntry) -> SkillInfo:
        path = self.skill_file(entry.name)
        info = SkillInfo(
            name=entry.name,
            role=entry.role,
            origin=entry.origin,
            modes=entry.modes,
            exists=path.is_file(),
            historical_use=entry.historical_use,
        )
        if info.exists:
            front = read_frontmatter(path)
            description = front.get("description")
            info.description = str(description).strip() if description else None
            info.version = _version_of(front)
            info.content_hash = content_hash(path)
        return info

    def skills(self) -> list[SkillInfo]:
        return [self.describe(entry) for entry in self.load().skills]

    def plan(self, mode: str) -> list[PlannedStep]:
        registry = self.load()
        definition = registry.modes.get(mode)
        if definition is None:
            raise ResourceNotFoundException(f"Execution mode '{mode}' is not defined.")
        entries = {e.name: e for e in registry.skills}
        steps: list[PlannedStep] = []
        for step in definition.steps:
            entry = entries.get(step.skill)
            path = self.skill_file(step.skill)
            reasons: list[str] = []
            availability: Availability = "available"
            if entry is None or not path.is_file():
                availability = "missing_skill"
                reasons.append(f"skill '{step.skill}' is not installed or registered")
            else:
                for req in entry.requires:
                    if req.kind == "setting" and req.name not in self._configured:
                        availability = "missing_setting"
                        reasons.append(f"setting {req.name.upper()} is not configured")
                    elif req.kind == "env" and not os.environ.get(req.name):
                        availability = "missing_setting"
                        reasons.append(f"environment variable {req.name} is not set")
                    elif req.kind == "file" and not Path(req.name).exists():
                        availability = "missing_setting"
                        reasons.append(f"required file {req.name} is missing")
                    elif req.kind == "mcp" and availability == "available":
                        availability = "runtime_check"
                        reasons.append(f"needs MCP server '{req.name}' at run time")
            steps.append(
                PlannedStep(
                    key=step.key,
                    skill=step.skill,
                    purpose=step.purpose,
                    optional=step.optional,
                    when=step.when,
                    group=step.group,
                    availability=availability,
                    reasons=reasons,
                    content_hash=content_hash(path) if path.is_file() else None,
                )
            )
        return steps

    def check(self) -> list[str]:
        """Consistency problems between the registry file and the skills on disk."""
        registry = self.load()
        problems: list[str] = []
        names = {e.name for e in registry.skills}
        for entry in registry.skills:
            if not self.skill_file(entry.name).is_file():
                problems.append(f"registered skill '{entry.name}' has no SKILL.md")
        for mode, definition in registry.modes.items():
            if definition.entry_skill not in names:
                problems.append(f"mode '{mode}' entry skill is not registered")
            for step in definition.steps:
                if step.skill not in names:
                    problems.append(f"mode '{mode}' step '{step.key}' is unregistered")
        return problems
