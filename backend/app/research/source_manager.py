"""Source manager: selection, deduplication, and provenance for research sources.

Search-result URLs are untrusted. This module runs each through ``UrlPolicy``,
deduplicates by normalized URL, caps the page budget, and turns extraction
output into ``ResearchSource`` records. Rejected sources are *kept* (with a
reason) rather than silently dropped.
"""

import asyncio
from dataclasses import dataclass, field

from app.core.hashing import content_hash  # noqa: F401  (re-exported)
from app.research.url_policy import UrlPolicy, UrlRejected, ValidatedUrl
from app.schemas.research import (
    ExtractedPage,
    ExtractionStatus,
    ResearchSource,
    SearchResult,
    utc_now,
)

VALIDATION_CONCURRENCY = 10


@dataclass(frozen=True)
class SourceCandidate:
    """A search result paired with its validation outcome."""

    result: SearchResult
    validated: ValidatedUrl | None = None
    rejection: UrlRejected | None = None

    @property
    def key(self) -> str:
        """Deduplication key: normalized URL when known, else the raw URL."""
        return self.validated.normalized if self.validated else self.result.url


@dataclass
class PreparedSources:
    """Outcome of validating and selecting search results."""

    selected: list[SourceCandidate] = field(default_factory=list)
    rejected: list[SourceCandidate] = field(default_factory=list)
    duplicate_count: int = 0
    over_budget_count: int = 0


class SourceManager:
    """Validate, select, and build sources. Holds no per-job state."""

    def __init__(self, policy: UrlPolicy | None = None) -> None:
        self._policy = policy or UrlPolicy()

    @property
    def policy(self) -> UrlPolicy:
        """The URL policy used for every fetch decision."""
        return self._policy

    async def prepare(
        self,
        results: list[SearchResult],
        *,
        max_sources: int,
        allowed_domains: list[str] | None = None,
    ) -> PreparedSources:
        """Validate ``results`` in order, drop duplicates, cap at ``max_sources``."""
        gate = asyncio.Semaphore(VALIDATION_CONCURRENCY)

        async def check(result: SearchResult) -> SourceCandidate:
            async with gate:
                try:
                    validated = await self._policy.validate(result.url, allowed_domains)
                except UrlRejected as exc:
                    return SourceCandidate(result, rejection=exc)
                return SourceCandidate(result, validated=validated)

        candidates = await asyncio.gather(*(check(r) for r in results))
        prepared = PreparedSources()
        seen: set[str] = set()
        for candidate in candidates:
            if candidate.key in seen:
                prepared.duplicate_count += 1
                continue
            seen.add(candidate.key)
            if candidate.rejection is not None:
                prepared.rejected.append(candidate)
            elif len(prepared.selected) < max_sources:
                prepared.selected.append(candidate)
            else:
                prepared.over_budget_count += 1
        return prepared

    async def verify_final_url(
        self, page: ExtractedPage, allowed_domains: list[str] | None = None
    ) -> str | None:
        """Return the provider-reported final URL only if it passes URL policy.

        Raises ``UrlRejected`` when the provider says the page ended up on an
        unsafe destination, so the caller can discard that content.
        """
        reported = page.metadata.get("provider_final_url")
        if not isinstance(reported, str) or not reported:
            return None
        validated = await self._policy.validate(reported, allowed_domains)
        return validated.normalized

    @staticmethod
    def rejected_source(candidate: SourceCandidate) -> ResearchSource:
        """Record a URL blocked by policy, with the reason code."""
        rejection = candidate.rejection
        return ResearchSource(
            original_url=candidate.result.url,
            normalized_url=None,
            domain=candidate.result.domain,
            title=candidate.result.title or None,
            snippet=candidate.result.snippet,
            extraction_status=ExtractionStatus.REJECTED,
            error_message=rejection.message if rejection else "rejected",
            metadata={
                "rejection_reason": rejection.reason if rejection else "unknown",
                "position": candidate.result.position,
            },
        )

    @staticmethod
    def build_source(
        candidate: SourceCandidate,
        status: ExtractionStatus,
        *,
        page: ExtractedPage | None = None,
        final_url: str | None = None,
        error: str | None = None,
    ) -> ResearchSource:
        """Build a source from a validated candidate and optional extraction."""
        validated = candidate.validated
        result = candidate.result
        markdown = (
            page.markdown if page and status == ExtractionStatus.SUCCEEDED else None
        )
        metadata: dict[str, object] = {"position": result.position}
        if page is not None:
            metadata.update(page.metadata)
        return ResearchSource(
            original_url=result.url,
            normalized_url=validated.normalized if validated else None,
            final_url=final_url,
            domain=validated.host if validated else result.domain,
            title=(page.title if page and page.title else result.title) or None,
            snippet=result.snippet,
            extracted_markdown=markdown,
            extraction_status=status,
            content_hash=page.content_hash if page and markdown else None,
            metadata=metadata,
            error_message=error,
            extracted_at=page.extracted_at if page else None,
            created_at=utc_now(),
        )
