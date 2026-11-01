"""Full-SERP parsing for SerpApi Google responses (Phase 3).

Pure functions over the decoded JSON payload: no network, no clock. Everything
returned is what the payload contained. Missing sections are reported as
absent (``None`` or empty lists), never filled in. The AI Overview is reported
as ``requires_second_request`` when the payload only carries a link to fetch
it, because AURORA does not make that second paid request here.
"""

from typing import Any
from urllib.parse import urlsplit

from pydantic import BaseModel, Field

from app.schemas.research import SearchResult
from app.tools.serpapi_tool import _host_of, _text

MAX_LIST = 20


class PeopleAlsoAsk(BaseModel):
    question: str = Field(max_length=500)
    snippet: str | None = Field(default=None, max_length=2000)
    url: str | None = Field(default=None, max_length=2048)
    domain: str | None = Field(default=None, max_length=253)


class FeaturedSnippet(BaseModel):
    kind: str = Field(
        max_length=64, description="Provider answer type, e.g. 'paragraph'"
    )
    text: str | None = Field(default=None, max_length=2000)
    url: str | None = Field(default=None, max_length=2048)
    domain: str | None = Field(default=None, max_length=253)


class AiOverview(BaseModel):
    """status: present | absent | requires_second_request | unknown."""

    status: str = "absent"
    text: str | None = Field(default=None, max_length=3000)
    cited_urls: list[str] = []
    cited_domains: list[str] = []


class SerpFeatures(BaseModel):
    people_also_ask: list[PeopleAlsoAsk] = []
    related_searches: list[str] = []
    featured_snippet: FeaturedSnippet | None = None
    ai_overview: AiOverview = AiOverview()
    shopping_results: bool = False
    local_results: bool = False
    ads_top: int = 0
    ads_bottom: int = 0
    total_results_reported: int | None = None


class SerpSnapshotData(BaseModel):
    """One observed SERP: organic results plus features, with the request parameters."""

    query: str
    country: str | None = None
    language: str | None = None
    device: str | None = None
    location: str | None = None
    provider_search_id: str | None = None
    organic: list[SearchResult] = []
    features: SerpFeatures = SerpFeatures()


def _urls_and_domains(items: Any) -> tuple[list[str], list[str]]:
    urls: list[str] = []
    domains: list[str] = []
    if not isinstance(items, list):
        return urls, domains
    for item in items[:MAX_LIST]:
        if not isinstance(item, dict):
            continue
        link = _text(item.get("link"), 2048)
        if link and urlsplit(link).scheme in ("http", "https"):
            urls.append(link)
            host = _host_of(link)
            if host and host not in domains:
                domains.append(host)
        else:
            source = _text(item.get("source") or item.get("displayed_link"), 253)
            if source and source not in domains:
                domains.append(source.lower())
    return urls, domains


def _people_also_ask(payload: dict[str, Any]) -> list[PeopleAlsoAsk]:
    out: list[PeopleAlsoAsk] = []
    for item in payload.get("related_questions") or []:
        if not isinstance(item, dict):
            continue
        question = _text(item.get("question"), 500)
        if not question:
            continue
        link = _text(item.get("link"), 2048)
        out.append(
            PeopleAlsoAsk(
                question=question,
                snippet=_text(item.get("snippet"), 2000),
                url=link
                if link and urlsplit(link).scheme in ("http", "https")
                else None,
                domain=_host_of(link) if link else None,
            )
        )
        if len(out) >= MAX_LIST:
            break
    return out


def _related_searches(payload: dict[str, Any]) -> list[str]:
    seen: list[str] = []
    for item in payload.get("related_searches") or []:
        if not isinstance(item, dict):
            continue
        query = _text(item.get("query"), 200)
        if query and query not in seen:
            seen.append(query)
        if len(seen) >= MAX_LIST:
            break
    return seen


def _featured_snippet(payload: dict[str, Any]) -> FeaturedSnippet | None:
    box = payload.get("answer_box") or payload.get("featured_snippet")
    if not isinstance(box, dict):
        return None
    link = _text(box.get("link"), 2048)
    text = _text(box.get("snippet") or box.get("answer") or box.get("result"), 2000)
    if text is None and link is None:
        return None
    return FeaturedSnippet(
        kind=_text(box.get("type"), 64) or "unknown",
        text=text,
        url=link if link and urlsplit(link).scheme in ("http", "https") else None,
        domain=_host_of(link) if link else None,
    )


def _ai_overview(payload: dict[str, Any]) -> AiOverview:
    block = payload.get("ai_overview")
    if block is None:
        return AiOverview(status="absent")
    if not isinstance(block, dict):
        return AiOverview(status="unknown")
    references = block.get("references") or block.get("sources") or []
    urls, domains = _urls_and_domains(references)
    text_blocks = block.get("text_blocks")
    text = None
    if isinstance(text_blocks, list):
        parts = [
            _text(b.get("snippet"), 500) for b in text_blocks if isinstance(b, dict)
        ]
        text = " ".join(p for p in parts if p)[:3000] or None
    if text or urls:
        return AiOverview(
            status="present", text=text, cited_urls=urls, cited_domains=domains
        )
    if block.get("page_token") or block.get("serpapi_link"):
        return AiOverview(status="requires_second_request")
    return AiOverview(status="unknown")


def parse_features(payload: dict[str, Any]) -> SerpFeatures:
    info = payload.get("search_information")
    total = info.get("total_results") if isinstance(info, dict) else None
    ads = payload.get("ads") or []
    top = bottom = 0
    if isinstance(ads, list):
        for ad in ads:
            if isinstance(ad, dict):
                if ad.get("block_position") == "bottom":
                    bottom += 1
                else:
                    top += 1
    return SerpFeatures(
        people_also_ask=_people_also_ask(payload),
        related_searches=_related_searches(payload),
        featured_snippet=_featured_snippet(payload),
        ai_overview=_ai_overview(payload),
        shopping_results=bool(payload.get("shopping_results")),
        local_results=bool(payload.get("local_results")),
        ads_top=top,
        ads_bottom=bottom,
        total_results_reported=total if isinstance(total, int) else None,
    )


def parse_serp(
    payload: dict[str, Any],
    *,
    query: str,
    organic: list[SearchResult],
    country: str | None,
    language: str | None,
    device: str | None,
    location: str | None,
) -> SerpSnapshotData:
    metadata = payload.get("search_metadata")
    search_id = _text(metadata.get("id"), 100) if isinstance(metadata, dict) else None
    return SerpSnapshotData(
        query=query,
        country=country,
        language=language,
        device=device,
        location=location,
        provider_search_id=search_id,
        organic=organic,
        features=parse_features(payload),
    )
