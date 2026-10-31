"""Best-effort robots.txt pre-filter for crawling.

AURORA does not scrape pages itself (Firecrawl does), so this is a courtesy
check with AURORA's own user agent, not a guarantee of what the provider will
fetch. The robots.txt URL is validated by ``UrlPolicy`` before it is requested,
redirects are not followed, and the body is size-capped. A missing or
unreachable robots.txt allows crawling (standard behaviour); every decision is
the caller's to audit.
"""

import logging
from collections.abc import Awaitable, Callable
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import httpx

from app.research.url_policy import UrlPolicy, UrlRejected

logger = logging.getLogger(__name__)

USER_AGENT = "AuroraResearchBot"
MAX_ROBOTS_BYTES = 512 * 1024

RobotsFetcher = Callable[[str], Awaitable[str | None]]


async def fetch_robots(url: str, timeout: float = 5.0) -> str | None:
    """GET ``url`` without following redirects; text on 200, else ``None``."""
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(
                url, follow_redirects=False, headers={"User-Agent": USER_AGENT}
            )
    except httpx.HTTPError:
        return None
    if response.status_code != 200 or len(response.content) > MAX_ROBOTS_BYTES:
        return None
    return response.text


class RobotsChecker:
    """Per-origin robots.txt cache and allow/deny decisions."""

    def __init__(
        self,
        policy: UrlPolicy,
        fetcher: RobotsFetcher = fetch_robots,
        user_agent: str = USER_AGENT,
    ) -> None:
        self._policy = policy
        self._fetcher = fetcher
        self._agent = user_agent
        self._cache: dict[str, RobotFileParser | None] = {}

    async def allowed(self, url: str) -> bool:
        """True if robots.txt does not forbid ``url`` for our user agent."""
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        if origin not in self._cache:
            self._cache[origin] = await self._load(origin)
        parser = self._cache[origin]
        return True if parser is None else parser.can_fetch(self._agent, url)

    async def _load(self, origin: str) -> RobotFileParser | None:
        robots_url = f"{origin}/robots.txt"
        try:
            await self._policy.validate(robots_url)
        except UrlRejected:
            return None
        text = await self._fetcher(robots_url)
        if text is None:
            return None
        parser = RobotFileParser()
        parser.parse(text.splitlines())
        return parser
