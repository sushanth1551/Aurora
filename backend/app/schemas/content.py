"""Pydantic schemas for content planning, drafting, and quality review."""

from enum import StrEnum

from pydantic import BaseModel, Field

from app.schemas.research import SourceReference


class ArticleStatus(StrEnum):
    """Editorial state; publishing requires explicit human approval."""

    DRAFT = "draft"
    PENDING_APPROVAL = "pending_approval"
    APPROVED = "approved"


class ContentBrief(BaseModel):
    """Plan for an SEO article."""

    title: str = Field(min_length=1, max_length=200)
    target_keyword: str = Field(min_length=1, max_length=200)
    audience: str | None = Field(default=None, max_length=500)
    secondary_keywords: list[str] = Field(default_factory=list, max_length=30)
    outline: list[str] = Field(default_factory=list, max_length=50)
    sources: list[SourceReference] = Field(default_factory=list)


class BlogArticle(BaseModel):
    """A drafted article with source provenance."""

    title: str = Field(min_length=1, max_length=200)
    slug: str | None = Field(default=None, max_length=200)
    meta_description: str | None = Field(default=None, max_length=320)
    body_markdown: str = Field(min_length=1)
    keywords: list[str] = Field(default_factory=list, max_length=30)
    sources: list[SourceReference] = Field(default_factory=list)
    status: ArticleStatus = ArticleStatus.DRAFT


class ContentQualityReport(BaseModel):
    """Outcome of factual, SEO, and editorial checks."""

    article_title: str = Field(max_length=200)
    seo_score: float | None = Field(default=None, ge=0, le=100)
    readability_score: float | None = Field(default=None, ge=0, le=100)
    unsupported_claims: list[str] = Field(default_factory=list)
    issues: list[str] = Field(default_factory=list)
    passed: bool = False
