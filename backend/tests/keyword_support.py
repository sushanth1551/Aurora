"""Offline fixtures for Phase 3: SerpApi-shaped payloads and a scripted SERP tool."""

from dataclasses import dataclass, field
from typing import Any

from app.schemas.research import SearchResult
from app.tools.errors import ProviderUnavailableError
from app.tools.serpapi_features import SerpSnapshotData, parse_serp


def organic(*items: tuple[str, str, str]) -> list[dict[str, Any]]:
    """(domain, title, snippet) -> SerpApi ``organic_results`` entries."""
    return [
        {
            "position": i,
            "title": title,
            "link": f"https://{domain}/p{i}",
            "snippet": snippet,
            "displayed_link": f"https://{domain}",
        }
        for i, (domain, title, snippet) in enumerate(items, start=1)
    ]


def payload(results: list[dict[str, Any]], **extra: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "search_metadata": {"id": "abc123"},
        "organic_results": results,
    }
    base.update(extra)
    return base


def search_results(results: list[dict[str, Any]]) -> list[SearchResult]:
    from urllib.parse import urlsplit

    return [
        SearchResult(
            position=r["position"],
            title=r["title"],
            url=r["link"],
            snippet=r.get("snippet"),
            domain=urlsplit(r["link"]).hostname,
        )
        for r in results
    ]


def snapshot(
    query: str, results: list[dict[str, Any]], **extra: Any
) -> SerpSnapshotData:
    data = payload(results, **extra)
    return parse_serp(
        data,
        query=query,
        organic=search_results(results),
        country="us",
        language="en",
        device="desktop",
        location=None,
    )


@dataclass
class FakeSerp:
    """Stands in for ``SerpApiTool.search_serp``: query -> payload; counts searches."""

    pages: dict[str, dict[str, Any]] = field(default_factory=dict)
    failing: set[str] = field(default_factory=set)
    calls: list[str] = field(default_factory=list)
    configured: bool = True

    def ensure_configured(self) -> None:
        if not self.configured:
            from app.core.exceptions import ConfigurationException

            raise ConfigurationException(
                "SerpApi is not configured; set SERPAPI_API_KEY."
            )

    async def search_serp(
        self,
        query: str,
        *,
        num_results: int = 10,
        country: str | None = None,
        language: str | None = None,
        device: str | None = None,
        location: str | None = None,
    ) -> SerpSnapshotData:
        self.calls.append(query)
        if query in self.failing:
            raise ProviderUnavailableError("SerpApi", "unavailable")
        data = self.pages.get(query) or payload([])
        results = data.get("organic_results", [])
        return parse_serp(
            data,
            query=query,
            organic=search_results(results),
            country=country,
            language=language,
            device=device,
            location=location,
        )


EMAIL_RESULTS = organic(
    (
        "rival-one.com",
        "Email Marketing Automation Guide",
        "Learn email marketing automation workflows.",
    ),
    (
        "rival-two.com",
        "Email Marketing Automation Tips",
        "Email marketing automation for small teams.",
    ),
    ("example.com", "Acme Email Platform", "Send campaigns with Acme."),
    (
        "rival-three.com",
        "Drip Campaign Examples",
        "Drip campaign examples and templates.",
    ),
    ("blog.test", "Welcome Series Examples", "Welcome series ideas."),
)
