"""API contracts for SEO Intelligence (``/api/v1/seo/intelligence``).

Domain models from ``app.seo.intelligence.models`` are reused as response
bodies; this module adds list envelopes and request bodies only.
"""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from app.seo.intelligence.models import (
    ExecutionIndexEntry,
    Finding,
    WebsiteConfig,
)
from app.seo.skills.improvement import SkillProposal
from app.seo.skills.registry import PlannedStep, SkillInfo


class WebsiteList(BaseModel):
    items: list[WebsiteConfig]
    total: int


class FindingList(BaseModel):
    items: list[Finding]
    total: int


class ExecutionList(BaseModel):
    items: list[ExecutionIndexEntry]
    total: int


class ExecutionPlan(BaseModel):
    site_id: str
    mode: str
    steps: list[PlannedStep]


class WebsiteContext(BaseModel):
    context: dict[str, Any]


class SkillList(BaseModel):
    items: list[SkillInfo]
    total: int
    problems: list[str]


class ProposalList(BaseModel):
    items: list[SkillProposal]
    total: int


class AuditImportRequest(BaseModel):
    """Import a finished AURORA technical audit into one website's knowledge."""

    model_config = ConfigDict(extra="forbid")

    confirm: Literal[True]
