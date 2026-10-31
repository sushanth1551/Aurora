"""Pydantic schemas for SEO analysis. No SEO functionality exists yet."""

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field, HttpUrl


class SearchIntent(StrEnum):
    """Dominant intent behind a search query."""

    INFORMATIONAL = "informational"
    NAVIGATIONAL = "navigational"
    COMMERCIAL = "commercial"
    TRANSACTIONAL = "transactional"


class FindingSeverity(StrEnum):
    """Severity of an SEO audit finding."""

    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class KeywordResearchRequest(BaseModel):
    """Input for keyword research."""

    topic: str = Field(min_length=2, max_length=500)
    locale: str = Field(default="en", min_length=2, max_length=16)
    max_keywords: int = Field(default=25, ge=1, le=200)


class KeywordInfo(BaseModel):
    """A keyword with metrics; metrics stay empty unless a provider supplied them."""

    keyword: str = Field(min_length=1, max_length=200)
    search_volume: int | None = Field(default=None, ge=0)
    difficulty: float | None = Field(default=None, ge=0, le=100)
    intent: SearchIntent | None = None
    source: str | None = Field(default=None, description="Provider of the metrics")


class SerpResult(BaseModel):
    """One organic search result."""

    position: int = Field(ge=1)
    title: str = Field(max_length=500)
    url: HttpUrl
    snippet: str | None = Field(default=None, max_length=2000)


class SeoFinding(BaseModel):
    """A single audit observation."""

    severity: FindingSeverity
    message: str = Field(max_length=1000)


class SeoAnalysisResponse(BaseModel):
    """Result of analysing a page or query."""

    target: str = Field(max_length=2000, description="URL or query analysed")
    score: float | None = Field(default=None, ge=0, le=100)
    findings: list[SeoFinding] = Field(default_factory=list)
    serp_results: list[SerpResult] = Field(default_factory=list)
    analyzed_at: datetime | None = None
