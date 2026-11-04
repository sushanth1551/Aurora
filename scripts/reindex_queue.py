"""Daily reindex queue for a configured website (see app/seo/intelligence/reindex.py).

    python scripts/reindex_queue.py check nextsteps    # batch due? (no network)
    python scripts/reindex_queue.py refresh nextsteps  # live site + GSC, build batch
    python scripts/reindex_queue.py confirm nextsteps <batch-id> [--paths /blog/a ...]
    python scripts/reindex_queue.py snooze nextsteps --hours 2
    python scripts/reindex_queue.py status nextsteps   # refresh, write status.md

Read-only towards the website and Search Console (URL Inspection and Search Analytics);
it never requests indexing itself. State: seo-intelligence/sites/<site>/reindex/.
Every command prints one JSON object.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import httpx  # noqa: E402

from app.core.config import Settings, get_settings  # noqa: E402
from app.core.exceptions import AuroraException  # noqa: E402
from app.research.url_policy import UrlPolicy  # noqa: E402
from app.seo.intelligence import reindex  # noqa: E402
from app.seo.intelligence.factory import build_seo_agent_service  # noqa: E402
from app.seo.intelligence.knowledge import write_atomic  # noqa: E402
from app.seo.intelligence.models import WebsiteConfig  # noqa: E402
from app.seo.intelligence.monitor import parse_change_log  # noqa: E402

MAX_BYTES = 2_000_000
LOC_RE = re.compile(r"<loc>\s*([^<\s]+)\s*</loc>")
DATE_RE = re.compile(r"\b(20\d\d-\d\d-\d\d)\b")
GSC_LAG_DAYS = 3
IDLE_RECHECK = timedelta(hours=6)
PENDING_REUSE = timedelta(hours=12)


def now_utc() -> datetime:
    return datetime.now(UTC)


class Store:
    def __init__(self, site_dir: Path) -> None:
        self.dir = site_dir / "reindex"
        self.state_file = self.dir / "state.json"
        self.log_file = self.dir / "requests.md"
        self.status_file = self.dir / "status.md"

    def load(self) -> reindex.ReindexState:
        if not self.state_file.exists():
            return reindex.ReindexState()
        return reindex.ReindexState.model_validate_json(
            self.state_file.read_text(encoding="utf-8")
        )

    def save(self, state: reindex.ReindexState) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        write_atomic(self.state_file, state.model_dump_json(indent=2) + "\n")

    def append_log(self, lines: list[str]) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        head = (
            ""
            if self.log_file.exists()
            else (
                "# Reindex requests\n\nOne line per page the owner requested "
                "indexing for (read by the SEO monitor's reindex checklist).\n\n"
            )
        )
        with self.log_file.open("a", encoding="utf-8") as fh:
            fh.write(head + "\n".join(lines) + "\n")


# -- live site -------------------------------------------------------------------


async def fetch(
    client: httpx.AsyncClient, policy: UrlPolicy, domain: str, url: str
) -> tuple[str | None, datetime | None]:
    """(body, Last-Modified) of one URL on the configured domain, or (None, None)."""
    try:
        checked = await policy.validate(url, allowed_domains=[domain])
        async with client.stream("GET", checked.normalized) as response:
            if response.status_code != 200:
                return None, None
            body = b""
            async for chunk in response.aiter_bytes():
                body += chunk
                if len(body) > MAX_BYTES:
                    return None, None
            modified = response.headers.get("last-modified")
            when = parsedate_to_datetime(modified) if modified else None
            return body.decode("utf-8", errors="replace"), when
    except Exception:  # unreachable pages are skipped this run
        return None, None


async def read_live(
    config: WebsiteConfig, state: reindex.ReindexState, now: datetime
) -> tuple[dict[str, object], dict[str, datetime]]:
    """Fingerprint every blog page in the live sitemap; also returns each post
    file's Last-Modified (the time of the deploy that put it live)."""
    base = config.base_url.rstrip("/")
    policy = UrlPolicy()
    changed: list[str] = []
    failed: list[str] = []
    deployed: dict[str, datetime] = {}
    async with httpx.AsyncClient(
        timeout=20, follow_redirects=False, headers={"User-Agent": "AURORA-Reindex"}
    ) as client:
        sitemap, _ = await fetch(client, policy, config.domain, base + "/sitemap.xml")
        if not sitemap:
            raise RuntimeError("sitemap.xml could not be read")
        paths = sorted(
            {
                "/" + loc.split("://", 1)[-1].split("/", 1)[-1].rstrip("/")
                for loc in LOC_RE.findall(sitemap)
                if "/blog/" in loc
            }
        )
        for path in paths:
            page, _ = await fetch(client, policy, config.domain, base + path)
            slug = path.rsplit("/", 1)[-1]
            body, modified = await fetch(
                client, policy, config.domain, f"{base}/blog-content/{slug}.json"
            )
            if page is None:
                failed.append(path)
                continue
            fp, title = reindex.fingerprint(page, body)
            if reindex.record_live(state, path, fp, title, now, deployed_at=modified):
                changed.append(path)
            if modified:
                deployed[path] = modified
        # Pages removed from the sitemap are no longer tracked.
        for path in list(state.pages):
            if path not in paths:
                del state.pages[path]
    return {"pages": len(paths), "changed": changed, "failed": failed}, deployed


def bootstrap(
    config: WebsiteConfig,
    state: reindex.ReindexState,
    deployed: dict[str, datetime],
) -> int:
    """First run: pages in the owner's change log changed at their live deploy time,
    and requests already logged in the reindex tracker count as requested."""
    repo = Path(config.repository.path or ".")
    paths = config.repository.seo_paths
    logged: set[str] = set()
    if paths.get("change_log"):
        try:
            text = (repo / paths["change_log"]).read_text(encoding="utf-8")
            logged = {r.path for r in parse_change_log(text) if r.path != "site"}
        except OSError:
            pass
    tracker: list[str] = []
    if paths.get("reindex_tracker"):
        try:
            tracker = (
                (repo / paths["reindex_tracker"])
                .read_text(encoding="utf-8")
                .splitlines()
            )
        except OSError:
            pass
    marked = 0
    for path, page in state.pages.items():
        if path not in logged:
            continue
        page.changed_at = deployed.get(path)
        slug = path.rsplit("/", 1)[-1]
        dates = [
            d
            for line in tracker
            if slug in line and "requested" in line.lower()
            for d in DATE_RE.findall(line)
        ]
        if dates and page.changed_at:
            requested = datetime.fromisoformat(max(dates)).replace(tzinfo=UTC)
            # A request on the deploy day counts (the tracker has no time of day).
            if requested.date() >= page.changed_at.date():
                page.requested_at = max(requested, page.changed_at)
        marked += 1
    return marked


# -- Search Console ----------------------------------------------------------------


async def read_google(
    settings: Settings,
    config: WebsiteConfig,
    state: reindex.ReindexState,
    now: datetime,
) -> dict[str, object]:
    from app.database.repositories.gsc_repository import GscRepository
    from app.database.session import create_session_factory, dispose_engine
    from app.monitoring.errors import GscStorageUnavailable
    from app.monitoring.service import RefreshTokenSource
    from app.monitoring.vault import select_vault
    from app.tools.search_console_tool import SearchConsoleTool

    property_id = config.integrations.search_console_property
    if not property_id:
        raise RuntimeError("no Search Console property in the site config")
    base = config.base_url.rstrip("/")
    inspected, errors = 0, []
    try:
        async with create_session_factory()() as session:
            repo = GscRepository(session)
            try:
                vault = select_vault(settings.gsc_token_storage)
            except GscStorageUnavailable:
                vault = None
            tokens = RefreshTokenSource(repo, vault, settings)
            tool = SearchConsoleTool(settings, refresh_token_provider=tokens.get)
            end = (now - timedelta(days=GSC_LAG_DAYS)).date()
            rows = await tool.query_performance(
                property_id,
                (end - timedelta(days=27)).isoformat(),
                end.isoformat(),
                dimensions=("page",),
            )
            impressions = {
                "/"
                + r.keys[0]
                .split("://", 1)[-1]
                .split("/", 1)[-1]
                .rstrip("/"): r.impressions
                for r in rows
            }
            for path, page in state.pages.items():
                page.impressions_28d = impressions.get(path, 0.0)
                if not reindex.needs_inspection(page):
                    continue
                try:
                    result = await tool.inspect_url(property_id, base + path)
                except Exception as exc:  # one failure does not stop the run
                    errors.append(f"{path}: {type(exc).__name__}")
                    continue
                inspected += 1
                crawl = result.get("lastCrawlTime")
                reindex.apply_inspection(
                    page,
                    datetime.fromisoformat(str(crawl).replace("Z", "+00:00"))
                    if crawl
                    else None,
                    str(result.get("coverageState") or ""),
                    now,
                )
    finally:
        await dispose_engine()
    return {"inspected": inspected, "errors": errors[:10]}


# -- commands ----------------------------------------------------------------------


def batch_view(
    config: WebsiteConfig, state: reindex.ReindexState, batch: reindex.Batch
) -> dict[str, object]:
    base = config.base_url.rstrip("/")
    prop = config.integrations.search_console_property or f"sc-domain:{config.domain}"

    def page(path: str) -> dict[str, object]:
        p = state.pages[path]
        return {
            "path": path,
            "url": base + path,
            "title": p.title,
            "changed": p.changed_at.astimezone().strftime("%Y-%m-%d %H:%M")
            if p.changed_at
            else "",
            "inspect": reindex.inspect_link(base, prop, path),
        }

    return {
        "id": batch.id,
        "date": batch.created_at.astimezone().strftime("%d %b %Y"),
        "pages": [page(p) for p in batch.pages],
        "newly_added": batch.newly_added,
        "crawled_since_last": batch.crawled_since_last,
        "remaining_after": batch.remaining_after,
    }


async def run(args: argparse.Namespace) -> dict[str, object]:
    settings = get_settings()
    agent = build_seo_agent_service(settings)
    config = agent.website(args.site)
    store = Store(agent.repository.site_dir(args.site))
    state = store.load()
    now = now_utc()

    if args.command == "check":
        nxt = reindex.next_batch_at(state)
        return {
            "due": reindex.is_due(state, now),
            "pending": state.pending.id if state.pending else None,
            "next_batch_at": nxt.isoformat() if nxt else None,
        }
    if args.command == "snooze":
        state.snooze_until = now + timedelta(hours=args.hours)
        store.save(state)
        return {"snoozed_until": state.snooze_until.isoformat()}
    if args.command == "confirm":
        lines = reindex.confirm(
            state, args.batch, now, only=args.paths or None, via=args.via
        )
        store.append_log(lines)
        store.save(state)
        write_atomic(store.status_file, reindex.status_markdown(state, now))
        return {"confirmed": len(lines), "log": str(store.log_file)}

    if args.command == "extra":
        # Owner-requested extra batch now (outside the 24-hour gap), from current state.
        batch = reindex.build_batch(state, now, only=args.paths)
        if batch is None:
            return {"due": False}
        store.save(state)
        return {"due": True, "batch": batch_view(config, state, batch)}

    # refresh / status: read the live site and Search Console.
    if (
        args.command == "refresh"
        and state.pending
        and reindex.is_due(state, now)
        and now - state.pending.created_at < PENDING_REUSE
    ):
        # A reminder for the batch already shown (after "Remind me later").
        return {"due": True, "batch": batch_view(config, state, state.pending)}
    if args.command == "refresh" and not reindex.is_due(state, now):
        nxt = reindex.next_batch_at(state)
        return {"due": False, "next_batch_at": nxt.isoformat() if nxt else None}
    first = not state.pages
    live, deployed = await read_live(config, state, now)
    if first:
        live["bootstrapped"] = bootstrap(config, state, deployed)
    google = await read_google(settings, config, state, now)
    out: dict[str, object] = {"live": live, "google": google}
    if args.command == "refresh":
        batch = reindex.build_batch(state, now)
        out["due"] = batch is not None
        if batch:
            out["batch"] = batch_view(config, state, batch)
        else:
            # Nothing to request: look again later for newly deployed changes.
            state.snooze_until = now + IDLE_RECHECK
    store.save(state)
    write_atomic(store.status_file, reindex.status_markdown(state, now))
    out["status_file"] = str(store.status_file)
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Daily reindex queue")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("check", "refresh", "status"):
        sub.add_parser(name).add_argument("site")
    p = sub.add_parser("confirm")
    p.add_argument("site")
    p.add_argument("batch")
    p.add_argument("--paths", nargs="+", help="record only these batch pages")
    p.add_argument("--via", default="Search Console URL Inspection")
    p = sub.add_parser("extra", help="owner-requested extra batch of these pages now")
    p.add_argument("site")
    p.add_argument("--paths", nargs="+", required=True)
    p = sub.add_parser("snooze")
    p.add_argument("site")
    p.add_argument("--hours", type=float, default=2.0)
    args = parser.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    try:
        result = asyncio.run(run(args))
    except (AuroraException, RuntimeError, ValueError) as exc:
        message = getattr(exc, "message", None) or str(exc)
        print(json.dumps({"error": type(exc).__name__, "message": message}))
        return 2
    # ASCII escapes: the PowerShell reminder reads this through the console code page.
    print(json.dumps(result, indent=2, ensure_ascii=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
