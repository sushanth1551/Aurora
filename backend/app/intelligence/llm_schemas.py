"""Structured outputs requested from the LLM. They are proposals: every ID and
citation in them is re-validated against the stored evidence before use."""

from pydantic import BaseModel, ConfigDict, Field

from app.intelligence.models import FindingKind, Level


class _Out(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)


class QuestionItem(_Out):
    question: str = Field(min_length=5, max_length=300)
    related_entity: str | None = Field(default=None, max_length=120)
    reason: str = Field(default="", max_length=300)
    priority: int = Field(default=3, ge=1, le=5)


class QuestionsOutput(_Out):
    questions: list[QuestionItem] = Field(default_factory=list, max_length=15)


class FindingItem(_Out):
    description: str = Field(min_length=5, max_length=500)
    kind: FindingKind = FindingKind.OBSERVATION
    related_entity: str | None = Field(default=None, max_length=120)
    reason: str = Field(default="", max_length=300)
    evidence_ids: list[str] = Field(default_factory=list, max_length=12)
    analytical_confidence: Level = Level.LOW


class GapItem(_Out):
    description: str = Field(min_length=5, max_length=300)
    related_entity: str | None = Field(default=None, max_length=120)
    predicate: str | None = Field(default=None, max_length=64)
    reason: str = Field(default="", max_length=300)
    priority: int = Field(default=3, ge=1, le=5)


class ContradictionItem(_Out):
    subject: str = Field(min_length=2, max_length=200)
    description: str = Field(min_length=5, max_length=400)
    evidence_ids: list[str] = Field(default_factory=list, max_length=12)


class AnalysisOutput(_Out):
    findings: list[FindingItem] = Field(default_factory=list, max_length=25)
    gaps: list[GapItem] = Field(default_factory=list, max_length=15)
    contradictions: list[ContradictionItem] = Field(default_factory=list, max_length=10)
    answered_questions: list[int] = Field(default_factory=list, max_length=15)
    sufficient: bool = False
    sufficiency_rationale: str = Field(default="", max_length=400)


class FollowupItem(_Out):
    query: str = Field(min_length=3, max_length=300)
    target_name: str | None = Field(default=None, max_length=120)
    reason: str = Field(default="", max_length=300)
    priority: int = Field(default=3, ge=1, le=5)


class FollowupOutput(_Out):
    queries: list[FollowupItem] = Field(default_factory=list, max_length=10)


class StatementItem(_Out):
    statement: str = Field(min_length=5, max_length=500)
    kind: FindingKind = FindingKind.OBSERVATION
    entity: str | None = Field(default=None, max_length=120)
    evidence_ids: list[str] = Field(default_factory=list, max_length=12)
    analytical_confidence: Level = Level.LOW


class ComparisonOutput(_Out):
    common_features: list[StatementItem] = Field(default_factory=list, max_length=15)
    differentiators: list[StatementItem] = Field(default_factory=list, max_length=15)
    positioning: list[StatementItem] = Field(default_factory=list, max_length=15)
    documented_strengths: list[StatementItem] = Field(
        default_factory=list, max_length=15
    )
    comparative_advantages: list[StatementItem] = Field(
        default_factory=list, max_length=15
    )
    observed_limitations: list[StatementItem] = Field(
        default_factory=list, max_length=15
    )
    hypotheses: list[StatementItem] = Field(default_factory=list, max_length=10)
    limitations: list[str] = Field(default_factory=list, max_length=10)


class StrategicItem(_Out):
    description: str = Field(min_length=5, max_length=500)
    evidence_ids: list[str] = Field(default_factory=list, max_length=12)
    related_entities: list[str] = Field(default_factory=list, max_length=10)
    rationale: str = Field(default="", max_length=400)
    limitations: list[str] = Field(default_factory=list, max_length=5)
    analytical_confidence: Level = Level.LOW


class MarketOutput(_Out):
    trends: list[StrategicItem] = Field(default_factory=list, max_length=8)
    opportunities: list[StrategicItem] = Field(default_factory=list, max_length=8)
    threats: list[StrategicItem] = Field(default_factory=list, max_length=8)
    conclusions: list[StatementItem] = Field(default_factory=list, max_length=12)
    limitations: list[str] = Field(default_factory=list, max_length=10)


class SummaryOutput(_Out):
    summary: list[StatementItem] = Field(default_factory=list, max_length=10)


class JudgeIssue(_Out):
    location: str = Field(max_length=120)
    problem: str = Field(max_length=300)


class JudgeOutput(_Out):
    issues: list[JudgeIssue] = Field(default_factory=list, max_length=20)
    note: str = Field(default="", max_length=400)
