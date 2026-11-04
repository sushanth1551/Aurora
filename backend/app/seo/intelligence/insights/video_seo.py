"""Video SEO: embeds without VideoObject markup, a video catalog and matches.

- **Embeds without markup**: pages that embed YouTube videos but carry no
  ``VideoObject`` JSON-LD (Google needs it to show the video in results).
- **Catalog**: metadata of the owner's playlists (title, upload date, duration,
  thumbnail, description) read with ``yt-dlp`` without downloading any media.
- **VideoObject proposals**: ready JSON-LD for each embedded catalog video, for
  the owner to review; nothing is written to the website.
- **Matches**: catalog videos whose topic matches a page that has no video, so
  existing videos (for example student testimonials) can strengthen pages.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any

from bs4 import BeautifulSoup

from app.seo.intelligence.insights.common import evidence, finding, page_path
from app.seo.intelligence.models import Finding

SKILL = "seo-video"
YOUTUBE_ID_RE = re.compile(
    r"(?:youtube(?:-nocookie)?\.com/(?:embed/|watch\?v=|shorts/)|youtu\.be/)([A-Za-z0-9_-]{11})"
)
STOP = {
    "the",
    "a",
    "an",
    "and",
    "or",
    "of",
    "to",
    "in",
    "on",
    "for",
    "with",
    "how",
    "what",
    "is",
    "are",
    "your",
    "you",
    "my",
    "i",
    "me",
    "it",
    "at",
    "by",
    "from",
    "next",
    "steps",
    "nextsteps",
    "nextstepsusmle",
    "usmle",
    "story",
    "video",
    "|",
    "-",
}


@dataclass
class Video:
    id: str
    title: str
    upload_date: str | None = None  # YYYY-MM-DD
    duration_seconds: int | None = None
    thumbnail: str | None = None
    description: str = ""

    @property
    def url(self) -> str:
        return f"https://www.youtube.com/watch?v={self.id}"


def embedded_ids(html: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    ids = []
    for tag in soup.find_all(["iframe", "a", "lite-youtube", "div"]):
        for attr in ("src", "href", "data-src", "videoid", "data-video-id"):
            value = tag.get(attr)
            if not value:
                continue
            m = YOUTUBE_ID_RE.search(str(value))
            vid = (
                m.group(1)
                if m
                else (
                    str(value)
                    if attr in ("videoid", "data-video-id") and len(str(value)) == 11
                    else None
                )
            )
            if vid and vid not in ids:
                ids.append(vid)
    return ids


def has_video_schema(html: str) -> bool:
    return bool(re.search(r'"@type"\s*:\s*"VideoObject"', html))


def iso_duration(seconds: int | None) -> str | None:
    if not seconds:
        return None
    h, rest = divmod(int(seconds), 3600)
    m, s = divmod(rest, 60)
    return "PT" + (f"{h}H" if h else "") + (f"{m}M" if m else "") + f"{s}S"


def video_object(video: Video) -> dict[str, Any]:
    data: dict[str, Any] = {
        "@context": "https://schema.org",
        "@type": "VideoObject",
        "name": video.title,
        "description": (video.description or video.title)[:500],
        "thumbnailUrl": video.thumbnail
        or f"https://i.ytimg.com/vi/{video.id}/hqdefault.jpg",
        "embedUrl": f"https://www.youtube.com/embed/{video.id}",
        "contentUrl": video.url,
    }
    if video.upload_date:
        data["uploadDate"] = video.upload_date
    duration = iso_duration(video.duration_seconds)
    if duration:
        data["duration"] = duration
    return data


def _tokens(text: str) -> set[str]:
    return {
        t
        for t in re.findall(r"[a-z0-9]+", text.lower())
        if t not in STOP and len(t) > 2
    }


def matches(
    catalog: list[Video],
    pages: dict[str, str],
    *,
    threshold: float = 0.3,
    per_page: int = 2,
) -> list[tuple[str, Video, float]]:
    """``pages``: URL -> page title, for pages without a video. Returns
    (page URL, video, overlap) sorted by overlap."""
    out = []
    for url, title in pages.items():
        page_tokens = _tokens(title + " " + page_path(url).replace("-", " "))
        scored = []
        for v in catalog:
            vt = _tokens(v.title)
            if not vt or not page_tokens:
                continue
            score = len(vt & page_tokens) / len(vt | page_tokens)
            if score >= threshold:
                scored.append((url, v, round(score, 2)))
        out += sorted(scored, key=lambda t: -t[2])[:per_page]
    return sorted(out, key=lambda t: -t[2])


def read_playlist(url: str, max_videos: int = 200) -> list[Video]:
    """Playlist metadata through yt-dlp (no media download). Needs the optional
    ``video`` extra: ``pip install -e ".[video]"``."""
    try:
        import yt_dlp  # noqa: PLC0415
    except ImportError as exc:
        raise RuntimeError(
            'yt-dlp is not installed; run .venv\\Scripts\\pip install -e ".[video]"'
        ) from exc
    options = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "extract_flat": "in_playlist",
        "noprogress": True,
    }
    with yt_dlp.YoutubeDL(options) as ydl:
        listing = ydl.extract_info(url, download=False) or {}
    ids = []
    for entry in listing.get("entries") or []:
        vid = entry.get("id") if isinstance(entry, dict) else None
        if vid and vid not in ids:
            ids.append(vid)
    videos = []
    detail_opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "noprogress": True,
    }
    with yt_dlp.YoutubeDL(detail_opts) as ydl:
        for vid in ids[:max_videos]:
            try:
                info = (
                    ydl.extract_info(
                        f"https://www.youtube.com/watch?v={vid}", download=False
                    )
                    or {}
                )
            except Exception:  # unavailable video: kept with its id only
                info = {}
            raw_date = str(info.get("upload_date") or "")
            videos.append(
                Video(
                    id=vid,
                    title=str(info.get("title") or vid),
                    upload_date=f"{raw_date[:4]}-{raw_date[4:6]}-{raw_date[6:8]}"
                    if len(raw_date) == 8
                    else None,
                    duration_seconds=int(info["duration"])
                    if info.get("duration")
                    else None,
                    thumbnail=info.get("thumbnail"),
                    description=str(info.get("description") or "")[:1000],
                )
            )
    return videos


def video_findings(
    html_by_url: dict[str, str], catalog: list[Video], titles: dict[str, str]
) -> tuple[list[Finding], dict[str, Any]]:
    by_id = {v.id: v for v in catalog}
    proposals: dict[str, list[dict[str, Any]]] = {}
    pages_with_video = set()
    out: list[Finding] = []
    for url, html in sorted(html_by_url.items()):
        ids = embedded_ids(html)
        if not ids:
            continue
        pages_with_video.add(url)
        if has_video_schema(html):
            continue
        proposals[url] = [
            video_object(by_id.get(i) or Video(id=i, title=titles.get(url, i)))
            for i in ids[:5]
        ]
        out.append(
            finding(
                key=f"videoobject-missing:{page_path(url)}",
                issue=f"{page_path(url)} embeds {len(ids)} YouTube video(s) without VideoObject markup",
                category="schema/video",
                priority="medium",
                evidence_items=[
                    evidence(
                        "AURORA crawl (embeds and JSON-LD)",
                        "video ids: " + ", ".join(ids[:5]),
                        url,
                    )
                ],
                seo_impact="Without VideoObject the videos are unlikely to appear as video results or key moments.",
                action="Add the proposed VideoObject JSON-LD (insights/videos-videoobject-proposals.json); "
                "fill missing upload dates from YouTube.",
                outcome="Every embedded video has valid VideoObject markup.",
                verification="Re-run /seo-site-crawl; validate with the Rich Results Test.",
                skill=SKILL,
                urls=[url],
            )
        )
    candidates = {
        u: t for u, t in titles.items() if u not in pages_with_video and "/blog/" in u
    }
    suggested = matches(catalog, candidates)
    if suggested:
        out.append(
            finding(
                key="video-embed-opportunities:site",
                issue=f"{len(suggested)} existing videos match pages that have no video",
                category="content/video",
                priority="low",
                evidence_items=[
                    evidence(
                        "Video catalog x page titles (topic overlap)",
                        "; ".join(
                            f"{page_path(u)} <- '{v.title}' ({s})"
                            for u, v, s in suggested[:10]
                        ),
                    )
                ],
                seo_impact="Relevant video adds first-hand experience (E-E-A-T), time on page and a chance at video results.",
                action="Embed the matching video with a one-line summary and VideoObject markup (owner review).",
                outcome="Matching pages carry a relevant video.",
                verification="Re-run /seo-site-crawl.",
                skill=SKILL,
                urls=[u for u, _, _ in suggested[:50]],
            )
        )
    details = {
        "pages_with_video": sorted(pages_with_video),
        "videoobject_proposals": proposals,
        "matches": [
            {"page": u, "video": asdict(v), "overlap": s} for u, v, s in suggested
        ],
        "catalog_size": len(catalog),
    }
    return out, details


def checkable_keys(html_by_url: dict[str, str], catalog: list[Video]) -> list[str]:
    keys = [f"videoobject-missing:{page_path(u)}" for u in html_by_url]
    return keys + (["video-embed-opportunities:site"] if catalog else [])
