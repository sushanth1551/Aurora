"""Daily reindex queue: which changed pages to request indexing for, and when.

Google offers no API for "Request indexing" on ordinary pages (the Indexing API is
limited to job postings and livestreams), so the owner clicks it in Search Console.
This module decides everything around that click:

* a page needs a request when its live title, description or body changed after
  Google's last crawl (``lastCrawlTime`` from the read-only URL Inspection API);
* a batch of up to ``BATCH_SIZE`` pages is offered 24 hours after the previous batch
  was confirmed, highest-traffic pages first;
* a confirmed page is not offered again for ``RE_REQUEST_DAYS`` unless Google still
  has not crawled it by then;
* pages Google re-crawled after their change drop out on their own.

Pure functions over ``ReindexState``; the script does the network calls.
"""

from __future__ import annotations

import hashlib
import html
import json
import re
from datetime import datetime, timedelta
from typing import Literal
from urllib.parse import quote

from pydantic import BaseModel, ConfigDict

BATCH_SIZE = 10
BATCH_GAP = timedelta(hours=24)
RE_REQUEST_DAYS = 7
TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)
DESC_RE = re.compile(
    r"<meta[^>]+name=[\"']description[\"'][^>]*content=[\"']([^\"']*)[\"']", re.I
)

Status = Literal["ok", "due", "requested", "unknown"]


class PageState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str
    fingerprint: str = ""
    title: str = ""
    #: When a live change to this page was first seen (None: no change tracked).
    changed_at: datetime | None = None
    last_crawl: datetime | None = None
    coverage: str = ""
    requested_at: datetime | None = None
    impressions_28d: float = 0.0
    status: Status = "unknown"


class Batch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    created_at: datetime
    pages: list[str]
    newly_added: list[str] = []
    crawled_since_last: list[str] = []
    remaining_after: int = 0


class ReindexState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pages: dict[str, PageState] = {}
    last_batch_at: datetime | None = None
    pending: Batch | None = None
    snooze_until: datetime | None = None
    #: Pages that were due at the previous batch (to report what is new).
    due_at_last_batch: list[str] = []
    #: Pages that were not yet crawled at the previous batch.
    uncrawled_at_last_batch: list[str] = []


def fingerprint(page_html: str, body_json: str | None) -> tuple[str, str]:
    """(fingerprint, title) of what Google would see: title, description, body.

    Build-specific markup (asset hashes) is ignored, so a redeploy of unchanged
    content keeps the same fingerprint.
    """
    title_m = TITLE_RE.search(page_html)
    title = " ".join(html.unescape(title_m.group(1)).split()) if title_m else ""
    desc_m = DESC_RE.search(page_html)
    desc = " ".join(html.unescape(desc_m.group(1)).split()) if desc_m else ""
    body = ""
    if body_json:
        try:
            body = json.dumps(json.loads(body_json), sort_keys=True, ensure_ascii=False)
        except ValueError:
            body = body_json
    digest = hashlib.sha256(f"{title}\n{desc}\n{body}".encode()).hexdigest()[:16]
    return digest, title


def record_live(
    state: ReindexState,
    path: str,
    fp: str,
    title: str,
    seen_at: datetime,
    *,
    deployed_at: datetime | None = None,
) -> bool:
    """Store a page's live fingerprint; returns True when it changed since last seen.

    ``deployed_at`` (the live file's Last-Modified) dates the change more precisely
    than the time we noticed it, when it is newer than our previous check.
    """
    page = state.pages.get(path)
    if page is None:
        state.pages[path] = PageState(path=path, fingerprint=fp, title=title)
        return False
    page.title = title
    if page.fingerprint == fp:
        return False
    page.fingerprint = fp
    page.changed_at = deployed_at if deployed_at and deployed_at <= seen_at else seen_at
    return True


def needs_inspection(page: PageState) -> bool:
    return page.status != "ok" or page.last_crawl is None


def apply_inspection(
    page: PageState, last_crawl: datetime | None, coverage: str, now: datetime
) -> None:
    """Set the page status from Google's last crawl time."""
    page.last_crawl = last_crawl
    page.coverage = coverage
    lower = coverage.lower()
    not_indexed = bool(coverage) and ("not indexed" in lower or "indexed" not in lower)
    if page.changed_at is None and not not_indexed:
        page.status = "ok"  # no change since we started tracking
        return
    if (
        not not_indexed
        and last_crawl
        and page.changed_at
        and last_crawl >= page.changed_at
    ):
        page.status = "ok"
        return
    if page.requested_at and (
        page.changed_at is None or page.requested_at >= page.changed_at
    ):
        if now - page.requested_at < timedelta(days=RE_REQUEST_DAYS):
            page.status = "requested"
            return
    page.status = "due"


def due_pages(state: ReindexState) -> list[PageState]:
    """Due pages, highest-traffic first, then oldest change first."""
    pages = [p for p in state.pages.values() if p.status == "due"]
    return sorted(
        pages,
        key=lambda p: (-p.impressions_28d, p.changed_at or datetime.max, p.path),
    )


def next_batch_at(state: ReindexState) -> datetime | None:
    """When the next batch may be offered (None: now)."""
    candidates = [
        t
        for t in (
            state.last_batch_at + BATCH_GAP if state.last_batch_at else None,
            state.snooze_until,
        )
        if t
    ]
    return max(candidates) if candidates else None


def is_due(state: ReindexState, now: datetime) -> bool:
    when = next_batch_at(state)
    return when is None or now >= when


def build_batch(
    state: ReindexState, now: datetime, only: list[str] | None = None
) -> Batch | None:
    """The next batch, or None when nothing is due. Also stored as ``pending``.

    ``only`` builds an owner-requested extra batch of exactly those pages (each must be
    waiting for a request), ignoring the 24-hour gap; the caller decides when.
    """
    due = due_pages(state)
    if only is not None:
        waiting = {p.path for p in due}
        not_due = sorted(set(only) - waiting)
        if not_due:
            raise ValueError(f"not waiting for a request: {', '.join(not_due)}")
        if len(only) > BATCH_SIZE:
            raise ValueError(f"at most {BATCH_SIZE} pages per batch")
        due = [p for p in due if p.path in set(only)]
    if not due:
        state.pending = None
        return None
    chosen = [p.path for p in due[:BATCH_SIZE]]
    previous_due = set(state.due_at_last_batch)
    crawled = [
        path
        for path in state.uncrawled_at_last_batch
        if path in state.pages and state.pages[path].status == "ok"
    ]
    batch = Batch(
        id=now.strftime("%Y%m%d-%H%M"),
        created_at=now,
        pages=chosen,
        newly_added=sorted(p.path for p in due if p.path not in previous_due),
        crawled_since_last=sorted(crawled),
        remaining_after=max(0, len(due) - len(chosen)),
    )
    state.pending = batch
    return batch


def confirm(
    state: ReindexState,
    batch_id: str,
    now: datetime,
    only: list[str] | None = None,
    via: str = "Search Console URL Inspection",
) -> list[str]:
    """Mark the pending batch as requested; returns the log lines to append.

    ``only`` records just those batch pages (for example when Google's daily quota
    stopped the run); the others stay due and come back in the next batch.
    """
    batch = state.pending
    if batch is None or batch.id != batch_id:
        raise ValueError(f"no pending batch {batch_id}")
    if only is not None:
        unknown = sorted(set(only) - set(batch.pages))
        if unknown:
            raise ValueError(f"not in batch {batch_id}: {', '.join(unknown)}")
    requested = [p for p in batch.pages if only is None or p in only]
    lines = []
    for path in requested:
        page = state.pages[path]
        page.requested_at = now
        page.status = "requested"
        lines.append(f"- `{path}` requested {now:%Y-%m-%d} (batch {batch.id}, {via})")
    state.due_at_last_batch = [p.path for p in due_pages(state)] + requested
    state.uncrawled_at_last_batch = sorted(
        p.path for p in state.pages.values() if p.status in {"due", "requested"}
    )
    state.last_batch_at = now
    state.pending = None
    state.snooze_until = None
    return lines


def inspect_link(base_url: str, property_id: str, path: str) -> str:
    """Search Console URL Inspection page for one URL (the owner clicks Request indexing)."""
    return (
        "https://search.google.com/search-console/inspect"
        f"?resource_id={quote(property_id, safe='')}"
        f"&id={quote(base_url.rstrip('/') + path, safe='')}"
    )


def status_markdown(state: ReindexState, now: datetime) -> str:
    """Every tracked page with its change date, Google's last crawl and status."""
    label = {
        "ok": "Google has the new version",
        "due": "needs a request",
        "requested": "requested, waiting for Google",
        "unknown": "not checked yet",
    }

    def fmt(t: datetime | None) -> str:
        return t.astimezone().strftime("%Y-%m-%d %H:%M") if t else "–"

    rows = sorted(
        state.pages.values(),
        key=lambda p: (
            ["due", "requested", "unknown", "ok"].index(p.status),
            -(p.changed_at.timestamp() if p.changed_at else 0),
            p.path,
        ),
    )
    counts = {s: sum(1 for p in rows if p.status == s) for s in label}
    nxt = next_batch_at(state)
    lines = [
        "# Reindex status",
        "",
        f"Checked {fmt(now)} (local time). Next batch: "
        f"{fmt(nxt) if nxt and nxt > now else 'now'}.",
        "",
        ", ".join(f"{label[s]}: {n}" for s, n in counts.items() if n),
        "",
        "| Page | Title | Live change seen | Google's last crawl | Requested | Status |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for p in rows:
        lines.append(
            f"| {p.path} | {p.title.replace('|', '/')[:70]} | {fmt(p.changed_at)} | "
            f"{fmt(p.last_crawl)} | {fmt(p.requested_at)} | {label[p.status]} |"
        )
    return "\n".join(lines) + "\n"
