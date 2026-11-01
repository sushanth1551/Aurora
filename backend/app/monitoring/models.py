"""Domain models for Search Console monitoring (no persistence or HTTP here).

``position`` everywhere is Search Console's *average position*: for each
impression Google reports the position of the topmost result of the site, and
the metric averages those values. It depends on the mix of queries, locations
and devices, so it is not an exact rank. Aggregates computed by AURORA weight the
daily positions by impressions; they can differ slightly from the Search Console UI.
"""

from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, Field

Level = Literal["high", "medium", "low"]
Kind = Literal[
    "low_ctr",
    "declining_clicks",
    "declining_position",
    "new_query",
    "cannibalization",
    "growing_page",
    "no_data_yet",
]
POSITION_NOTE = (
    "Average position is the average of the topmost position of your site across impressions. "
    "It is not exact rank tracking: it changes with the query mix, location, device and personalization."
)


class Agg(BaseModel):
    """Totals for one page, query or (page, query) over a window."""

    page: str | None = None
    query: str | None = None
    clicks: float = 0.0
    impressions: float = 0.0
    position_weighted: float = 0.0  # sum(position * impressions)

    @property
    def ctr(self) -> float:
        return self.clicks / self.impressions if self.impressions else 0.0

    @property
    def position(self) -> float | None:
        return self.position_weighted / self.impressions if self.impressions else None


class Thresholds(BaseModel):
    """Analysis parameters. Every default is a documented heuristic, not a benchmark."""

    window_days: int = Field(default=28, ge=7, le=90)
    min_impressions: int = Field(default=100, ge=10, le=100_000)
    low_ctr_ratio: float = Field(
        default=0.5,
        gt=0.05,
        lt=1.0,
        description="CTR below this share of the property's own CTR at that position",
    )
    min_ctr_shortfall_clicks: float = Field(default=3.0, ge=0)
    decline_ratio: float = Field(default=0.3, gt=0.05, lt=1.0)
    min_previous_clicks: int = Field(default=20, ge=1)
    position_worsening: float = Field(default=2.0, gt=0.5)
    new_query_min_impressions: int = Field(default=10, ge=1)
    cannibal_min_impressions: int = Field(default=100, ge=10)
    cannibal_min_share: float = Field(default=0.2, gt=0.0, lt=0.5)
    min_baseline_rows: int = Field(default=20, ge=5)
    min_window_coverage: float = Field(default=0.8, gt=0.3, le=1.0)
    max_findings_per_kind: int = Field(default=25, ge=1, le=100)


class Window(BaseModel):
    current_start: date
    current_end: date
    previous_start: date
    previous_end: date
    days: int
    current_days_with_data: int
    previous_days_with_data: int
    history_start: date | None = None  # earliest stored date for the property

    @property
    def current_ok(self) -> bool:
        return self.current_days_with_data > 0

    def coverage(self, which: Literal["current", "previous"]) -> float:
        have = (
            self.current_days_with_data
            if which == "current"
            else self.previous_days_with_data
        )
        return have / self.days if self.days else 0.0


class InsightDraft(BaseModel):
    kind: Kind
    subject_type: Literal["page", "query", "property"]
    subject: str
    evidence: dict[str, Any]
    impact: Level
    confidence: Level
    effort: Level
    risk: Level
    tier: str
    action: str
    rationale: str
    verification: str
    basis: Literal["measured", "inferred"]
    refresh_candidate: bool = False
    article_id: str | None = None
    rank: int = 0
