"""Scheduled SEO monitor for a configured website (run every 14 days).

Tracks the owner's SEO changes in the website repository (read-only git), keeps
the change checklist, checks live titles, reads Search Console rankings through
AURORA's stored read-only connection, records a ``monitor`` execution and writes
the report. No paid APIs, no writes to the website, Search Console or git.

Usage (repository root)::

    python scripts/seo_monitor.py nextsteps
    python scripts/seo_monitor.py nextsteps --no-gsc --no-live   # offline run

Needs PostgreSQL running (it holds the sealed Search Console token) unless
``--no-gsc`` is given. Exit code 0 when a report was written (even partial),
2 when the run could not start.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import httpx  # noqa: E402

from app.core.config import Settings, get_settings  # noqa: E402
from app.core.exceptions import AuroraException  # noqa: E402
from app.research.url_policy import UrlPolicy  # noqa: E402
from app.seo.intelligence.changes import ChangeTracker, GitError  # noqa: E402
from app.seo.intelligence.factory import (  # noqa: E402
    build_seo_agent_service,
    build_skill_improvement,
)
from app.seo.intelligence.gsc_source import gsc_query  # noqa: E402
from app.seo.intelligence.models import Execution, WebsiteConfig  # noqa: E402
from app.seo.intelligence.monitor import (  # noqa: E402
    GscFetcher,
    LiveFetcher,
    MonitorService,
)
from app.seo.intelligence.pdf_report import (  # noqa: E402
    markdown_to_html,
    pdf_path,
    render_pdf,
)

MAX_GSC_REQUESTS = 5
MAX_PAGE_BYTES = 1_000_000


def gsc_fetcher(settings: Settings, property_id: str) -> GscFetcher:
    """Date x page rows through AURORA's stored Search Console credential."""
    query = gsc_query(settings, property_id, max_requests=MAX_GSC_REQUESTS)

    async def fetch(start: date, end: date) -> list[dict[str, object]]:
        return await query(start, end, ("date", "page"))

    return fetch


def live_fetcher(domain: str) -> LiveFetcher:
    """GET one page of the configured site; UrlPolicy-validated, no redirects."""
    policy = UrlPolicy()

    async def fetch(url: str) -> str | None:
        try:
            checked = await policy.validate(url, allowed_domains=[domain])
            async with httpx.AsyncClient(
                timeout=15,
                follow_redirects=False,
                headers={"User-Agent": "AURORA-SEO-Monitor"},
            ) as client:
                async with client.stream("GET", checked.normalized) as response:
                    if response.status_code != 200:
                        return None
                    body = b""
                    async for chunk in response.aiter_bytes():
                        body += chunk
                        if len(body) > MAX_PAGE_BYTES:
                            break
                    return body.decode("utf-8", errors="replace")
        except Exception:  # unreachable or rejected pages are reported as "not fetched"
            return None

    return fetch


async def run(args: argparse.Namespace) -> int:
    settings = get_settings()
    agent = build_seo_agent_service(settings)
    config = agent.website(args.site)
    try:
        tracker = ChangeTracker(config) if config.repository.path else None
    except GitError:
        tracker = None
    property_id = config.integrations.search_console_property
    monitor = MonitorService(
        agent,
        build_skill_improvement(settings),
        tracker=tracker,
        gsc=None
        if args.no_gsc or not property_id
        else gsc_fetcher(settings, property_id),
        live=None if args.no_live else live_fetcher(config.domain),
    )
    execution = await monitor.run(args.site)
    report = agent.repository.site_dir(args.site) / "reports" / f"{execution.id}.md"
    output = {
        "execution_id": execution.id,
        "status": execution.status,
        "blockers": execution.blockers,
        "report": str(report),
        "pdf": None,
        "open_pdf": config.scheduler.open_pdf,
    }
    if config.scheduler.pdf_folder and not args.no_pdf:
        try:
            output["pdf"] = str(write_pdf(config, execution, report))
        except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
            output["pdf_error"] = str(exc)[:300]
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    print(json.dumps(output, indent=2))
    return 0


def write_pdf(config: WebsiteConfig, execution: Execution, report: Path) -> Path:
    """PDF copy of the report in the configured folder, named with the run date."""
    day = execution.started_at.astimezone().date()
    target = pdf_path(
        Path(config.scheduler.pdf_folder or "."), config.scheduler.pdf_name, day
    )
    title = config.scheduler.pdf_name.replace("{date}", day.isoformat())
    subtitle = (
        f"{config.name} ({config.base_url}) · run {execution.id} · "
        f"status {execution.status}"
    )
    page = markdown_to_html(report.read_text(encoding="utf-8"), title, subtitle)
    return render_pdf(page, target)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Scheduled SEO monitor")
    parser.add_argument("site")
    parser.add_argument("--no-gsc", action="store_true", help="skip Search Console")
    parser.add_argument("--no-live", action="store_true", help="skip live page fetches")
    parser.add_argument(
        "--no-pdf", action="store_true", help="do not write the PDF report"
    )
    try:
        return asyncio.run(run(parser.parse_args(argv)))
    except AuroraException as exc:
        print(json.dumps({"error": type(exc).__name__, "message": exc.message}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
