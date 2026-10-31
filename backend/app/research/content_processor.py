"""Content processing: normalize extracted Markdown into attributable documents.

Keeps headings, tables, lists and fenced code; removes obvious navigation,
cookie banners and boilerplate that repeats across a site's pages. The original
extraction is stored untouched next to the normalized text, every document keeps
its source URL and metadata, and nothing about a page (canonical URL, dates) is
invented: those fields are filled only from reliable provider metadata.
"""

import logging
import re
from collections import Counter
from dataclasses import dataclass
from urllib.parse import urlsplit

from app.core.config import Settings, get_settings
from app.core.hashing import content_hash
from app.core.logging import log_event
from app.research.domains import same_site
from app.research.url_policy import UrlPolicy, UrlRejected
from app.schemas.intelligence import (
    ContentSection,
    DocumentStatus,
    PageType,
    ProcessedDocument,
)
from app.schemas.research import ExtractedPage

logger = logging.getLogger(__name__)

MAX_SECTION_CHARS = 6000
BOILERPLATE_MIN_DOCS = 3
BOILERPLATE_SHARE = 0.6
_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
_FENCE = re.compile(r"^\s*(```|~~~)")
_ZERO_WIDTH = re.compile("[​‌‍﻿]")
_LINK_ONLY = re.compile(
    r"^\s*(?:[-*+]\s+)?(?:\[[^\]]{1,60}\]\([^)]*\)\s*[|·•\-–]?\s*)+$"
)
_BOILERPLATE_LINE = re.compile(
    r"(we use cookies|accept all cookies|cookie (settings|preferences|policy)|"
    r"skip to (main )?content|all rights reserved|©\s*\d{4}|"
    r"subscribe to our newsletter|sign up for our newsletter)",
    re.IGNORECASE,
)
_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}")
_CANONICAL_KEYS = ("canonical", "canonicalUrl", "ogUrl")
_PUBLISHED_KEYS = ("publishedTime", "article:published_time")
_MODIFIED_KEYS = ("modifiedTime", "article:modified_time")


@dataclass(frozen=True)
class CrawledPage:
    """A scraped page together with what we know about it."""

    url: str
    page_type: PageType
    page: ExtractedPage


def _is_boilerplate_candidate(line: str) -> bool:
    stripped = line.strip()
    return (
        bool(stripped)
        and len(stripped) < 200
        and not stripped.startswith(("#", "|", ">", "```"))
    )


def _normalize_basic(markdown: str) -> list[str]:
    text = _ZERO_WIDTH.sub("", markdown).replace("\r\n", "\n").replace("\r", "\n")
    return [line.rstrip() for line in text.split("\n")]


def _split_code(lines: list[str]) -> list[tuple[bool, str]]:
    """Tag each line with whether it is inside a fenced code block."""
    tagged: list[tuple[bool, str]] = []
    in_code = False
    for line in lines:
        if _FENCE.match(line):
            tagged.append((True, line))
            in_code = not in_code
        else:
            tagged.append((in_code, line))
    return tagged


def _drop_navigation(tagged: list[tuple[bool, str]]) -> list[tuple[bool, str]]:
    """Remove cookie/boilerplate lines and link-only nav runs at the page edges."""
    kept = [
        (code, line)
        for code, line in tagged
        if code or not (_BOILERPLATE_LINE.search(line) and len(line) < 200)
    ]
    first_heading = next(
        (i for i, (code, line) in enumerate(kept) if not code and _HEADING.match(line)),
        len(kept),
    )

    def link_run(indices: range) -> set[int]:
        run: list[int] = []
        drop: set[int] = set()
        for i in indices:
            code, line = kept[i]
            if not code and _LINK_ONLY.match(line):
                run.append(i)
                continue
            if line.strip():
                if len(run) >= 5:
                    drop.update(run)
                run = []
        if len(run) >= 5:
            drop.update(run)
        return drop

    drop = link_run(range(0, first_heading))
    drop |= link_run(range(max(first_heading, len(kept) - 15), len(kept)))
    return [item for i, item in enumerate(kept) if i not in drop]


def _collapse_blank(lines: list[str]) -> list[str]:
    result: list[str] = []
    blanks = 0
    for line in lines:
        if line.strip():
            blanks = 0
            result.append(line)
        else:
            blanks += 1
            if blanks <= 1:
                result.append("")
    while result and not result[0].strip():
        result.pop(0)
    while result and not result[-1].strip():
        result.pop()
    return result


def split_sections(markdown: str) -> list[ContentSection]:
    """Split on headings (outside code), then cap section size by paragraphs."""
    sections: list[ContentSection] = []
    heading: str | None = None
    level = 0
    buffer: list[str] = []
    order = 0

    def flush() -> None:
        nonlocal order
        text = "\n".join(buffer).strip()
        if not text and heading is None:
            return
        for chunk in _chunk(text, MAX_SECTION_CHARS):
            sections.append(
                ContentSection(
                    section_id=f"s{order}",
                    heading=heading,
                    level=level,
                    text=chunk,
                    order=order,
                )
            )
            order += 1

    for in_code, line in _split_code(markdown.split("\n")):
        match = None if in_code else _HEADING.match(line)
        if match:
            flush()
            buffer = []
            level = len(match.group(1))
            heading = match.group(2).strip()
        else:
            buffer.append(line)
    flush()
    return sections


def _chunk(text: str, limit: int) -> list[str]:
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    current = ""
    for paragraph in text.split("\n\n"):
        while len(paragraph) > limit:  # a single oversized paragraph
            chunks.append(paragraph[:limit])
            paragraph = paragraph[limit:]
        if current and len(current) + len(paragraph) + 2 > limit:
            chunks.append(current)
            current = paragraph
        else:
            current = f"{current}\n\n{paragraph}" if current else paragraph
    if current:
        chunks.append(current)
    return chunks


class ContentProcessor:
    """Normalize pages into ``ProcessedDocument`` objects."""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._policy = UrlPolicy()

    def process(self, items: list[CrawledPage]) -> list[ProcessedDocument]:
        """Process a website's pages; detects repeated boilerplate and duplicates."""
        cleaned = [self._clean(item) for item in items]
        boilerplate = self._repeated_lines(cleaned)
        documents: list[ProcessedDocument] = []
        seen_hashes: dict[str, str] = {}
        for item, (lines, truncated) in zip(items, cleaned, strict=True):
            if boilerplate:
                lines = [line for line in lines if line.strip() not in boilerplate]
            normalized = "\n".join(_collapse_blank(lines))
            documents.append(self._document(item, normalized, truncated, seen_hashes))
        log_event(
            logger,
            "content_processed",
            documents=len(documents),
            duplicates=sum(d.status == DocumentStatus.DUPLICATE for d in documents),
            empty=sum(d.status == DocumentStatus.EMPTY for d in documents),
        )
        return documents

    # -- internals ------------------------------------------------------------

    def _clean(self, item: CrawledPage) -> tuple[list[str], bool]:
        markdown = item.page.markdown or ""
        limit = int(self._settings.website_research_max_content_size_mb * 1024 * 1024)
        truncated = len(markdown.encode("utf-8")) > limit
        if truncated:
            markdown = markdown.encode("utf-8")[:limit].decode("utf-8", errors="ignore")
        tagged = _drop_navigation(_split_code(_normalize_basic(markdown)))
        return [line for _, line in tagged], truncated

    @staticmethod
    def _repeated_lines(cleaned: list[tuple[list[str], bool]]) -> set[str]:
        """Lines present in most of a site's pages are navigation/footer boilerplate."""
        if len(cleaned) < BOILERPLATE_MIN_DOCS:
            return set()
        counts: Counter[str] = Counter()
        for lines, _ in cleaned:
            counts.update(
                {
                    line.strip()
                    for _, line in _split_code(lines)
                    if _is_boilerplate_candidate(line)
                }
            )
        threshold = max(BOILERPLATE_MIN_DOCS, int(len(cleaned) * BOILERPLATE_SHARE))
        # Repeated plain-text/link lines only; headings, tables and code are exempt.
        return {
            line for line, n in counts.items() if n >= threshold and len(line) < 160
        }

    def _document(
        self,
        item: CrawledPage,
        normalized: str,
        truncated: bool,
        seen: dict[str, str],
    ) -> ProcessedDocument:
        page = item.page
        meta = dict(page.metadata)
        document = ProcessedDocument(
            source_url=item.url,
            normalized_url=item.url,
            canonical_url=self._canonical(item.url, meta),
            title=page.title,
            page_type=item.page_type,
            original_markdown=page.markdown or "",
            original_hash=content_hash(page.markdown) if page.markdown else None,
            metadata={
                **meta,
                "description": page.description,
                "provider_status": page.provider_status,
                "extracted_at": page.extracted_at.isoformat(),
            },
            published_at=self._date(meta, _PUBLISHED_KEYS),
            modified_at=self._date(meta, _MODIFIED_KEYS),
            truncated=truncated,
        )
        if not normalized.strip():
            document.status = DocumentStatus.EMPTY
            return document
        digest = content_hash(normalized)
        document.normalized_markdown = normalized
        document.content_hash = digest
        if digest in seen:
            document.status = DocumentStatus.DUPLICATE
            document.duplicate_of = seen[digest]
            return document
        seen[digest] = item.url
        document.sections = split_sections(normalized)
        return document

    def _canonical(self, page_url: str, meta: dict[str, object]) -> str | None:
        """Accept a provider canonical URL only if it is safe and on the same site."""
        for key in _CANONICAL_KEYS:
            value = meta.get(key)
            if not isinstance(value, str):
                continue
            try:
                normalized, host = self._policy.normalize(value)
            except UrlRejected:
                continue
            if same_site(host, urlsplit(page_url).hostname or ""):
                return normalized
        return None

    @staticmethod
    def _date(meta: dict[str, object], keys: tuple[str, ...]) -> str | None:
        for key in keys:
            value = meta.get(key)
            if isinstance(value, str) and _ISO_DATE.match(value.strip()):
                return value.strip()
        return None
