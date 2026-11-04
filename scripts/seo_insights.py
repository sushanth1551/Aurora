"""SEO Intelligence insight runs: opportunities, alerts, rank tracking, keyword data,
AI visibility, free site crawl (technical + YMYL + video + source map), video
catalog, competitor watch, exports, dashboard data and approval-gated rollback.

Each analysis is one execution in the knowledge base (start -> record -> finish)
unless ``--execution <id>`` is given, in which case it records into that running
execution (for example a running ``/seo-audit``) and does not finish it.

Examples (from the AURORA folder):
    python scripts/seo_insights.py opportunities nextsteps
    python scripts/seo_insights.py opportunities nextsteps --conversions ga4-landing.csv
    python scripts/seo_insights.py alerts nextsteps --pdf
    python scripts/seo_insights.py ranks nextsteps                 # Search Console only, free
    python scripts/seo_insights.py ranks nextsteps --serp --max-searches 8   # paid, budgeted
    python scripts/seo_insights.py keyword-data nextsteps          # free unless DataForSEO set up
    python scripts/seo_insights.py ai-visibility nextsteps
    python scripts/seo_insights.py record-ai-answer nextsteps --prompt "best usmle coaching in india" --engine perplexity --date 2026-11-04 --cited a.com,b.com --no-mention
    python scripts/seo_insights.py crawl nextsteps --max-pages 150
    python scripts/seo_insights.py videos nextsteps --playlist https://www.youtube.com/playlist?list=...
    python scripts/seo_insights.py competitors nextsteps
    python scripts/seo_insights.py export nextsteps
    python scripts/seo_insights.py dashboard nextsteps
    python scripts/seo_insights.py rollback-plan nextsteps 8108c8c
    python scripts/seo_insights.py rollback-prepare nextsteps 8108c8c --approved-by "Owner name"

Search Console runs need the database container (AURORA's stored credential).
Paid calls (SerpApi, DataForSEO) only run within the budgets in the site config.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import httpx  # noqa: E402

from app.core.config import Settings, get_settings  # noqa: E402
from app.core.exceptions import AuroraException  # noqa: E402
from app.research.robots import USER_AGENT  # noqa: E402
from app.research.url_policy import UrlPolicy  # noqa: E402
from app.seo.intelligence.factory import (  # noqa: E402
    build_insight_store,
    build_seo_agent_service,
)
from app.seo.intelligence.gsc_source import gsc_inspect, gsc_query  # noqa: E402
from app.seo.intelligence.insights import (  # noqa: E402
    ai_visibility,
    alerts,
    competitor_watch,
    exports,
    keyword_data,
    market_ranks,
    opportunities,
    rollback,
    site_crawl,
    source_map,
    video_seo,
    ymyl,
)
from app.seo.intelligence.insights.common import (  # noqa: E402
    aggregate_blog,
    blog_scoped,
    page_path,
    rows_of,
    slug,
)
from app.seo.intelligence.insights.store import InsightStore  # noqa: E402
from app.seo.intelligence.models import (  # noqa: E402
    CheckedScope,
    ExecutionPayload,
    Finding,
    ReportSection,
    SkillUse,
    ToolUse,
    WebsiteConfig,
)
from app.seo.intelligence.pdf_report import (  # noqa: E402
    markdown_to_html,
    pdf_path,
    render_pdf,
)
from app.seo.intelligence.service import SeoAgentService  # noqa: E402
from app.seo.technical.site_files import fetch_site_file  # noqa: E402

GSC_LAG_DAYS = 3
# Per-post checks reported as one blog-wide finding (ledger style: long-titles:blog).
BLOG_CHECKS = (
    "image-dimensions-missing",
    "unsourced-numbers",
    "unsupported-claim",
    "stale-year",
    "thin-content",
    "videoobject-missing",
)
BLOG_LABELS = {
    "image-dimensions-missing": "Images without width/height",
    "unsourced-numbers": "Fees, rates or pass figures without an official source nearby",
    "unsupported-claim": "Claim terms (superlatives or promises)",
    "stale-year": "Past years presented as current",
    "thin-content": "Posts under the site's minimum word count",
    "videoobject-missing": "Embedded videos without VideoObject markup",
}


@dataclass
class Outcome:
    """What one analysis produced; turned into an ExecutionPayload."""

    skill: str
    reason: str
    summary: str
    findings: list[Finding] = field(default_factory=list)
    tools: list[ToolUse] = field(default_factory=list)
    checked: CheckedScope = field(default_factory=CheckedScope)
    blockers: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    sections: list[ReportSection] = field(default_factory=list)
    pages: list[str] = field(default_factory=list)
    actions: list[str] = field(default_factory=list)
    pdf: str | None = None


@dataclass
class Context:
    settings: Settings
    agent: SeoAgentService
    store: InsightStore
    config: WebsiteConfig
    today: date
    args: argparse.Namespace

    @property
    def data_end(self) -> date:
        return self.today - timedelta(days=GSC_LAG_DAYS)

    @property
    def base(self) -> str:
        return self.config.base_url.rstrip("/")


def ledger_keys(ctx: Context, prefixes: tuple[str, ...]) -> list[str]:
    """Active ledger keys an analysis re-examines in full (all pages/queries)."""
    return [
        f.key
        for f in ctx.agent.repository.load_findings(ctx.config.id)
        if f.status in ("open", "in_progress", "implemented")
        and f.key.startswith(prefixes)
    ]


ASSET_SUFFIXES = (
    ".pdf",
    ".jpg",
    ".jpeg",
    ".png",
    ".webp",
    ".gif",
    ".svg",
    ".mp4",
    ".zip",
    ".xml",
    ".txt",
)


def is_html_path(path: str) -> bool:
    return not path.lower().endswith(ASSET_SUFFIXES)


def md_table(headers: list[str], rows: list[list[Any]]) -> str:
    if not rows:
        return "_None._"
    lines = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    for r in rows:
        lines.append("| " + " | ".join(str(c).replace("|", "/") for c in r) + " |")
    return "\n".join(lines)


def gsc_or_blocker(
    ctx: Context,
) -> Callable[..., Awaitable[list[dict[str, object]]]] | None:
    prop = ctx.config.integrations.search_console_property
    return gsc_query(ctx.settings, prop) if prop else None


async def read_gsc(
    ctx: Context, out: Outcome, start: date, end: date, dims: tuple[str, ...]
) -> list[Any] | None:
    query = gsc_or_blocker(ctx)
    if query is None:
        out.blockers.append("No Search Console property in the site config.")
        return None
    try:
        rows = rows_of(await query(start, end, dims), dims)
    except Exception as exc:  # reported, never hidden
        out.blockers.append(
            f"Search Console not read ({type(exc).__name__}: {str(exc)[:160]}); "
            "start Docker Desktop / the database container and retry."
        )
        out.tools.append(
            ToolUse(
                name="Google Search Console API",
                kind="api",
                status="failed",
                note=str(exc)[:200],
            )
        )
        return None
    out.tools.append(
        ToolUse(
            name=f"Google Search Console API ({' x '.join(dims)})",
            kind="api",
            status="success",
            calls=1,
            note=f"{start}..{end}, {len(rows)} rows",
        )
    )
    return rows


# --- Commands ---------------------------------------------------------------------------


async def cmd_opportunities(ctx: Context) -> Outcome:
    out = Outcome(
        skill=opportunities.SKILL,
        reason="Find quick wins, decaying content and page value from Search Console.",
        summary="",
    )
    end = ctx.data_end
    recent = await read_gsc(ctx, out, end - timedelta(days=27), end, ("query", "page"))
    previous = await read_gsc(
        ctx, out, end - timedelta(days=55), end - timedelta(days=28), ("query", "page")
    )
    daily = await read_gsc(ctx, out, end - timedelta(days=179), end, ("date", "page"))
    if recent is None or daily is None:
        out.summary = "Opportunities not computed: Search Console data unavailable."
        return out
    curve = opportunities.ctr_curve(recent)
    opps = opportunities.striking_distance(recent, curve) + opportunities.low_ctr(
        recent, curve
    )
    if ctx.args.conversions:
        conv = opportunities.read_conversions_file(Path(ctx.args.conversions))
        clicks: dict[str, float] = {}
        for r in recent:
            clicks[page_path(r.get("page"))] = (
                clicks.get(page_path(r.get("page")), 0) + r.clicks
            )
        opportunities.add_leads(opps, opportunities.page_values(conv, clicks))
        out.tools.append(
            ToolUse(
                name=f"Conversions file {Path(ctx.args.conversions).name}",
                kind="file",
                status="success",
                note=f"{len(conv)} pages",
            )
        )
        out.notes.append(
            "Extra leads = extra clicks x the page's own conversions per click (periods as supplied)."
        )
    crawl_snap = ctx.store.latest(ctx.config.id, "site-crawl")
    excluded = tuple(ctx.config.excluded_paths)
    # Known pages: crawled HTML pages only (assets and PDFs have no word count).
    known = [
        page_path(p["url"])
        for p in (crawl_snap or {}).get("data", {}).get("pages", [])
        if p.get("status") == 200 and p.get("words") is not None
    ]
    trends = [
        t
        for t in opportunities.page_trends(
            daily,
            end,
            known_pages=known,
            queries_recent=recent,
            queries_previous=previous or [],
        )
        if not (excluded and t.page.startswith(excluded)) and is_html_path(t.page)
    ]
    out.findings = opportunities.opportunity_findings(
        opps, ctx.base
    ) + opportunities.trend_findings(trends, ctx.base)
    # Low CTR is only judged where the test applies (top-5 rows with enough
    # impressions); other low-ctr findings stay "not rechecked".
    scope = opportunities.low_ctr_scope(recent)
    out.checked.keys = (
        ledger_keys(
            ctx,
            (
                "striking-distance:",
                "content-decay:",
                "prune-review:",
                "no-impressions:",
            ),
        )
        + [k for k in ledger_keys(ctx, ("low-ctr:",)) if k.split(":", 1)[1] in scope]
        + [f.key for f in out.findings]
    )
    if not curve:
        out.notes.append(
            "Too little data for the site's own CTR curve: extra clicks are not estimated."
        )
    ctx.store.save_snapshot(
        ctx.config.id,
        "opportunities",
        ctx.today,
        opportunities.snapshot(opps, trends, curve),
    )
    top = [o for o in opps if o.kind == "striking-distance"][:10]
    out.sections.append(
        ReportSection(
            title="Striking-distance queries (top 10)",
            markdown=md_table(
                ["Query", "Page", "Pos", "Impr", "CTR", "Extra clicks", "Extra leads"],
                [
                    [
                        o.query,
                        o.page,
                        o.position,
                        int(o.impressions),
                        f"{o.ctr:.1%}",
                        o.extra_clicks if o.extra_clicks is not None else "n/a",
                        o.extra_leads or "",
                    ]
                    for o in top
                ],
            ),
        )
    )
    decaying = [t for t in trends if t.status in ("decaying", "dead")]
    out.sections.append(
        ReportSection(
            title="Decaying and dormant pages",
            markdown=md_table(
                ["Page", "Status", "Decision", "Clicks 28d", "Prev 28d", "Best 28d"],
                [
                    [
                        t.page,
                        t.status,
                        t.decision + (f" -> {t.merge_with}" if t.merge_with else ""),
                        int(t.recent.clicks),
                        int(t.previous.clicks),
                        int(t.peak.clicks),
                    ]
                    for t in decaying[:20]
                ],
            ),
        )
    )
    out.summary = (
        f"{len(top)} striking-distance pages, {sum(o.kind == 'low-ctr' for o in opps)} low-CTR queries, "
        f"{sum(t.status == 'decaying' for t in trends)} decaying and {sum(t.status == 'dead' for t in trends)} dormant pages."
    )
    return out


async def cmd_alerts(ctx: Context) -> Outcome:
    out = Outcome(
        skill=alerts.SKILL,
        reason="Weekly early warning on clicks, impressions, positions and index status.",
        summary="",
    )
    end = ctx.data_end
    start = end - timedelta(days=7 * (alerts.BASELINE_WEEKS + 1) - 1)
    site = await read_gsc(ctx, out, start, end, ("date",))
    pages = await read_gsc(ctx, out, start, end, ("date", "page"))
    queries = await read_gsc(ctx, out, start, end, ("date", "query"))
    found: list[alerts.Alert] = []
    if site is not None and pages is not None and queries is not None:
        found = alerts.detect(site, pages, queries, end, ctx.config.alerts)
        out.checked.keys += ledger_keys(
            ctx,
            (
                "traffic-drop:",
                "impressions-drop:",
                "page-clicks-drop:",
                "query-position-drop:",
            ),
        )
    if (
        ctx.config.alerts.inspect_key_pages
        and ctx.config.integrations.search_console_property
        and not ctx.args.no_inspect
    ):
        inspect = gsc_inspect(
            ctx.settings, ctx.config.integrations.search_console_property
        )
        results = {}
        for path in ctx.config.key_pages[:10]:
            url = ctx.base + (path if path.startswith("/") else "/" + path)
            try:
                results[url] = await inspect(url)
            except Exception as exc:
                out.notes.append(
                    f"URL Inspection failed for {path}: {type(exc).__name__}"
                )
        out.tools.append(
            ToolUse(
                name="Google URL Inspection API",
                kind="api",
                status="success" if results else "failed",
                calls=len(results),
            )
        )
        found += alerts.index_alerts(results)
        out.checked.keys += [f"index-status:{page_path(u)}" for u in results]
    out.findings = alerts.alert_findings(found, ctx.base)
    ctx.store.save_snapshot(
        ctx.config.id,
        "alerts",
        ctx.today,
        {"alerts": alerts.snapshot(found), "data_end": end.isoformat()},
    )
    out.sections.append(
        ReportSection(
            title="Alerts",
            markdown=md_table(
                ["Priority", "Kind", "Subject", "Detail"],
                [[a.priority, a.kind, a.subject, a.detail] for a in found],
            ),
        )
    )
    out.summary = (
        f"{len(found)} alert(s) for the 7 days ending {end}."
        if found
        else f"No alerts for the 7 days ending {end}."
    )
    if ctx.args.pdf and found:
        folder = Path(ctx.config.scheduler.pdf_folder or ROOT / "data" / "reports")
        markdown = (
            f"# SEO Alerts {ctx.config.name}\n\n{out.summary}\n\n"
            + out.sections[0].markdown
        )
        target = render_pdf(
            markdown_to_html(markdown, f"SEO Alerts {ctx.config.name}"),
            pdf_path(folder, "SEO Alerts {date}", ctx.today),
        )
        out.pdf = str(target)
    return out


async def cmd_ranks(ctx: Context) -> Outcome:
    out = Outcome(
        skill=market_ranks.SKILL,
        reason="Track keyword positions per market.",
        summary="",
    )
    rt = ctx.config.rank_tracking
    keywords = rt.keywords or ctx.config.seed_keywords
    if not keywords:
        out.blockers.append(
            "No keywords: set rank_tracking.keywords or seed_keywords in the site config."
        )
        return out
    end = ctx.data_end
    rows = await read_gsc(
        ctx,
        out,
        end - timedelta(days=rt.gsc_window_days - 1),
        end,
        ("query", "country"),
    )
    ranks: list[market_ranks.MarketRank] = []
    if rows is not None:
        history = ctx.store.read_json(ctx.config.id, "rank-history.json", [])
        ranks = market_ranks.gsc_market_ranks(rows, keywords, rt.markets, end, history)
        history = [
            h for h in history if h.get("period_end") != end.isoformat()
        ] + market_ranks.history_entries(ranks)
        ctx.store.write_json(ctx.config.id, "rank-history.json", history)
        out.checked.keys += [
            f"keyword-ranking-drop:{slug(k)}:{m.lower()}"
            for k in keywords
            for m in rt.markets
        ]
    checks: list[market_ranks.SerpCheck] = []
    if ctx.args.serp:
        usage = ctx.store.read_json(ctx.config.id, "serp-usage.json", {})
        left = market_ranks.budget_left(usage, rt, ctx.today)
        last = usage.get("last_check")
        due = (
            not last
            or (ctx.today - date.fromisoformat(last)).days >= rt.serp_check_every_days
            or ctx.args.force
        )
        if rt.serpapi_monthly_budget == 0:
            out.blockers.append(
                "SerpApi checks need a monthly budget: set rank_tracking.serpapi_monthly_budget (owner approval)."
            )
        elif not due:
            out.notes.append(
                f"SERP checks not due (last {last}, every {rt.serp_check_every_days} days); use --force."
            )
        elif left == 0:
            out.blockers.append("This month's SerpApi budget is used up.")
        else:
            from app.tools.serpapi_tool import SerpApiTool

            tool = SerpApiTool(ctx.settings)
            if not tool.is_configured:
                out.blockers.append("SERPAPI_API_KEY is not configured.")
            else:
                cap = min(left, ctx.args.max_searches or left)
                checks, spent, notes = await market_ranks.serp_checks(
                    tool,
                    keywords,
                    rt,
                    ctx.config.domain,
                    ctx.config.language,
                    ctx.today,
                    cap,
                )
                month = market_ranks.month_key(ctx.today)
                usage[month] = int(usage.get(month, 0)) + spent
                usage["last_check"] = ctx.today.isoformat()
                ctx.store.write_json(ctx.config.id, "serp-usage.json", usage)
                out.notes += notes
                out.tools.append(
                    ToolUse(
                        name="SerpApi (Google)",
                        kind="api",
                        status="success" if checks else "failed",
                        calls=spent,
                        note=f"{usage[month]}/{rt.serpapi_monthly_budget} searches this month",
                    )
                )
                out.checked.keys += [
                    f"featured-snippet-lost:{slug(c.keyword)}:{c.market.lower()}"
                    for c in checks
                ]
    out.findings = market_ranks.rank_findings(ranks, checks, ctx.base)
    ctx.store.save_snapshot(
        ctx.config.id, "rank-tracking", ctx.today, market_ranks.snapshot(ranks, checks)
    )
    out.sections.append(
        ReportSection(
            title=f"Positions per market (Search Console, {rt.gsc_window_days} days to {end})",
            markdown=md_table(
                ["Keyword", "Market", "Position", "Previous", "Move", "Impr", "Clicks"],
                [
                    [
                        r.keyword,
                        r.market,
                        r.position if r.position is not None else "no impressions",
                        r.previous_position or "",
                        r.movement if r.movement is not None else "",
                        int(r.impressions),
                        int(r.clicks),
                    ]
                    for r in ranks
                ],
            ),
        )
    )
    if checks:
        out.sections.append(
            ReportSection(
                title="Live SERP checks",
                markdown=md_table(
                    [
                        "Keyword",
                        "Market",
                        "Device",
                        "Our pos",
                        "Snippet",
                        "AI Overview",
                        "Cites us",
                    ],
                    [
                        [
                            c.keyword,
                            c.market,
                            c.device,
                            c.our_position or "-",
                            c.featured_snippet_domain or "-",
                            c.ai_overview,
                            c.ai_overview_cites_site,
                        ]
                        for c in checks
                    ],
                ),
            )
        )
    ranked = [r for r in ranks if r.position is not None]
    out.summary = f"{len(ranked)}/{len(ranks)} keyword-market pairs have Search Console impressions; {len(checks)} live SERP checks."
    return out


async def cmd_keyword_data(ctx: Context) -> Outcome:
    out = Outcome(
        skill=keyword_data.SKILL,
        reason="Demand per market (free Search Console + optional DataForSEO).",
        summary="",
    )
    end = ctx.data_end
    rows = await read_gsc(ctx, out, end - timedelta(days=27), end, ("query", "country"))
    if rows is None:
        return out
    markets = ctx.config.rank_tracking.markets
    keywords = (
        [k.strip() for k in ctx.args.keywords.split(",")]
        if ctx.args.keywords
        else keyword_data.candidates(ctx.config, rows)
    )
    data = keyword_data.gsc_demand(rows, keywords, markets)
    settings = ctx.config.keyword_data
    summaries: dict[str, dict[str, Any]] = {}
    if settings.provider == "dataforseo":
        from app.tools.dataforseo_tool import DataForSeoTool

        tool = DataForSeoTool(ctx.settings)
        usage = ctx.store.read_json(ctx.config.id, "keyword-usage.json", {})
        spent_month = keyword_data.month_spent(usage, ctx.today)
        if not tool.is_configured:
            out.blockers.append(
                "DATAFORSEO_LOGIN / DATAFORSEO_PASSWORD are not configured."
            )
        elif settings.monthly_budget_usd <= spent_month:
            out.blockers.append(
                f"DataForSEO monthly budget ${settings.monthly_budget_usd} is used up (${spent_month:.2f})."
            )
        else:
            cost, notes = await keyword_data.enrich(
                tool, data, settings, ctx.config.language, spent_month
            )
            out.notes += notes
            if ctx.args.backlinks:
                targets = [ctx.config.domain] + [
                    c.domain for c in ctx.config.competitors if c.relevance == "direct"
                ]
                for target in targets:
                    if spent_month + (tool.cost_usd) >= settings.monthly_budget_usd:
                        out.notes.append(
                            "Budget reached before all backlink summaries."
                        )
                        break
                    try:
                        summaries[target] = await tool.backlinks_summary(target)
                    except Exception as exc:
                        out.notes.append(
                            f"Backlinks for {target} failed: {type(exc).__name__}"
                        )
                out.checked.keys.append("referring-domain-gap:site")
            month = ctx.today.strftime("%Y-%m")
            usage[month] = round(spent_month + tool.cost_usd, 4)
            ctx.store.write_json(ctx.config.id, "keyword-usage.json", usage)
            out.tools.append(
                ToolUse(
                    name="DataForSEO",
                    kind="api",
                    status="success",
                    note=f"${tool.cost_usd:.4f} this run, ${usage[month]:.2f} this month (reported by DataForSEO)",
                )
            )
            out.checked.keys += [
                f"keyword-gap:{slug(r.keyword)}:{r.market.lower()}"
                for r in data
                if r.search_volume is not None
            ]
            _ = cost
    else:
        out.notes.append(
            "keyword_data.provider is 'none': volumes and difficulty not measured (Search Console demand only)."
        )
    out.findings = keyword_data.keyword_findings(
        data, ctx.base
    ) + keyword_data.backlink_findings(summaries, ctx.config.domain)
    ctx.store.save_snapshot(
        ctx.config.id,
        "keyword-data",
        ctx.today,
        {"keywords": keyword_data.snapshot(data), "backlinks": summaries},
    )
    out.sections.append(
        ReportSection(
            title="Keyword demand per market",
            markdown=md_table(
                [
                    "Keyword",
                    "Market",
                    "GSC impr (28d)",
                    "GSC pos",
                    "Volume",
                    "KD",
                    "CPC",
                ],
                [
                    [
                        r.keyword,
                        r.market,
                        int(r.gsc_impressions),
                        r.gsc_position or "-",
                        r.search_volume if r.search_volume is not None else "n/m",
                        r.keyword_difficulty
                        if r.keyword_difficulty is not None
                        else "n/m",
                        r.cpc if r.cpc is not None else "n/m",
                    ]
                    for r in data[:60]
                ],
            ),
        )
    )
    out.summary = f"{len(keywords)} keywords x {len(markets)} markets; provider: {settings.provider}."
    return out


async def cmd_ai_visibility(ctx: Context) -> Outcome:
    out = Outcome(
        skill=ai_visibility.SKILL,
        reason="AI crawler access, llms.txt, AI Overview citations and the prompt panel.",
        summary="",
    )
    policy = UrlPolicy()
    allowed = [ctx.config.domain, "www." + ctx.config.domain]
    async with httpx.AsyncClient(
        timeout=20, follow_redirects=False, headers={"User-Agent": USER_AGENT}
    ) as client:
        robots = await fetch_site_file(
            ctx.base + "/robots.txt",
            policy=policy,
            client=client,
            allowed_domains=allowed,
            max_bytes=500_000,
        )
        llms = await fetch_site_file(
            ctx.base + "/llms.txt",
            policy=policy,
            client=client,
            allowed_domains=allowed,
            max_bytes=500_000,
        )
    out.tools.append(
        ToolUse(
            name="HTTP GET robots.txt, llms.txt (UrlPolicy)",
            kind="aurora",
            status="success",
            calls=2,
        )
    )
    robots_text = robots.text if robots.outcome == "ok" else None
    if robots.outcome not in ("ok", "client_error"):
        out.notes.append(f"robots.txt fetch outcome: {robots.outcome}")
    paths = list(
        dict.fromkeys(
            ["/", "/blog"] + [p for p in ctx.config.key_pages if p.startswith("/")]
        )
    )
    access = ai_visibility.crawler_access(robots_text, ctx.base, paths)
    explicit = (
        ai_visibility.explicit_ai_agents(robots_text)
        if robots.outcome in ("ok", "client_error")
        else None
    )
    llms_result = ai_visibility.check_llms_txt(llms.status, llms.text)
    snap = ctx.store.latest(ctx.config.id, "rank-tracking")
    checks = []
    if snap and (ctx.today - date.fromisoformat(snap["date"])).days <= 30:
        checks = [
            market_ranks.SerpCheck(**c) for c in snap["data"].get("serp_checks", [])
        ]
    raw_answers = ctx.store.read_json(ctx.config.id, "ai-answers.json", [])
    answers = [ai_visibility.AiAnswer(**a) for a in raw_answers]
    sov = ai_visibility.share_of_voice(answers, ctx.config.domain)
    out.findings = (
        ai_visibility.access_findings(
            access, ctx.config.ai_visibility, ctx.base, explicit
        )
        + ai_visibility.llms_findings(llms_result, ctx.base)
        + ai_visibility.overview_findings(checks)
        + ai_visibility.panel_findings(
            answers, ctx.config.domain, ctx.config.brand_name
        )
    )
    out.checked.keys += ["ai-answer-crawlers-blocked:site", "llms-txt:site"]
    if explicit is not None:
        out.checked.keys.append("robots-ai-crawlers:site")
    if ctx.config.ai_visibility.allow_training_crawlers is not None:
        out.checked.keys.append("ai-training-crawler-policy:site")
    out.checked.keys += [
        f"ai-overview-not-cited:{slug(c.keyword)}:{c.market.lower()}" for c in checks
    ]
    out.checked.keys += [f"ai-prompt-gap:{slug(a.prompt)}" for a in answers]
    ctx.store.save_snapshot(
        ctx.config.id,
        "ai-visibility",
        ctx.today,
        ai_visibility.snapshot(access, llms_result, checks, sov),
    )
    out.sections.append(
        ReportSection(
            title="AI crawler access (robots.txt, RFC 9309)",
            markdown=md_table(
                ["Agent", "Operator", "Purpose", "Blocked on"],
                [
                    [a.agent, a.operator, a.purpose, ", ".join(a.blocked_paths) or "-"]
                    for a in access
                ],
            ),
        )
    )
    if sov["engines"]:
        out.sections.append(
            ReportSection(
                title="Prompt panel: share of voice",
                markdown=md_table(
                    [
                        "Engine",
                        "Answers",
                        "Cite the site",
                        "Mention the brand",
                        "Share cited",
                    ],
                    [
                        [
                            e,
                            v["answers"],
                            v["cited"],
                            v["mentioned"],
                            f"{v['share_cited_pct']}%",
                        ]
                        for e, v in sov["engines"].items()
                    ],
                ),
            )
        )
    if not ctx.config.ai_visibility.prompts:
        out.notes.append(
            "No prompts in ai_visibility.prompts; add the questions students ask AI assistants."
        )
    out.summary = (
        f"{sum(a.blocked for a in access)} of {len(access)} AI agents blocked somewhere; llms.txt "
        f"{'valid' if llms_result.valid else 'missing/invalid'}; {len(checks)} AI Overview observations; "
        f"{sov['answers_considered']} recorded AI answers."
    )
    return out


def cmd_record_answer(ctx: Context) -> int:
    a = ctx.args
    answer = ai_visibility.AiAnswer(
        prompt=a.prompt.strip(),
        engine=a.engine,
        observed_on=a.date,
        cited_domains=[d.strip() for d in (a.cited or "").split(",") if d.strip()],
        mentions_brand=a.mention,
        excerpt=a.excerpt or "",
        observed_by=a.observed_by or "",
    )
    answers = ctx.store.read_json(ctx.config.id, "ai-answers.json", [])
    answers.append(answer.__dict__)
    ctx.store.write_json(ctx.config.id, "ai-answers.json", answers)
    print(json.dumps({"recorded": answer.__dict__, "total": len(answers)}, indent=2))
    return 0


async def cmd_crawl(ctx: Context) -> Outcome:
    out = Outcome(
        skill=site_crawl.SKILL,
        reason="Free repeatable crawl with the technical rules, YMYL, video and source-map checks.",
        summary="",
    )
    output = await site_crawl.crawl(
        ctx.config, ctx.settings, max_pages=ctx.args.max_pages
    )
    r = output.result
    out.tools.append(
        ToolUse(
            name="AURORA crawler (direct HTML, UrlPolicy)",
            kind="aurora",
            status="success" if not r.coverage.stopped_reason else "partial",
            calls=r.usage.direct_requests,
            note=f"{r.coverage.pages_audited} pages audited",
        )
    )
    findings = site_crawl.convert(
        output.rule_findings, ctx.base
    ) + site_crawl.extra_findings(output, ctx.config)
    excluded = tuple(ctx.config.excluded_paths)
    html = {
        u: h
        for u, h in output.html.items()
        if not (excluded and page_path(u).startswith(excluded))
    }
    ymyl_keys: list[str] = []
    video_keys: list[str] = []
    if not ctx.args.no_ymyl:
        pages = [
            ymyl.check_page(u, h, ctx.config.ymyl, ctx.today) for u, h in html.items()
        ]
        findings += ymyl.accuracy_findings(pages, ctx.config.ymyl, ctx.today)
        ymyl_keys = ymyl.checkable_keys(pages, ctx.config.ymyl)
        out.actions.append(
            f"YMYL checks on {len(pages)} pages ({len(ctx.config.ymyl.facts)} registry facts)"
        )
    titles = {
        (p.final_url or p.url): (
            p.signals.titles[0] if p.signals and p.signals.titles else ""
        )
        for p in r.pages
    }
    if not ctx.args.no_video:
        catalog = [
            video_seo.Video(**v)
            for v in ctx.store.read_json(ctx.config.id, "videos-catalog.json", [])
        ]
        vf, details = video_seo.video_findings(html, catalog, titles)
        findings += vf
        ctx.store.write_json(
            ctx.config.id,
            "videos-videoobject-proposals.json",
            details["videoobject_proposals"],
        )
        video_keys = video_seo.checkable_keys(html, catalog)
    findings = aggregate_blog(findings, BLOG_CHECKS, BLOG_LABELS)
    repo_path = ctx.config.repository.path
    if repo_path and Path(repo_path).is_dir() and not ctx.args.no_source_map:
        try:
            index = source_map.build_index(Path(repo_path))
            strings = source_map.page_strings(
                {
                    (p.final_url or p.url): (
                        p.signals.titles[0] if p.signals and p.signals.titles else None,
                        p.signals.meta_descriptions[0]
                        if p.signals and p.signals.meta_descriptions
                        else None,
                    )
                    for p in r.pages
                    if p.signals
                }
            )
            n = source_map.attach(findings, index, strings)
            out.tools.append(
                ToolUse(
                    name="git ls-files + text search (read-only)",
                    kind="git",
                    status="success",
                    note=f"{len(index.files)} files, {n} locations",
                )
            )
            out.notes += [f"Site source: {f}" for f in index.framework]
        except Exception as exc:
            out.notes.append(
                f"Source map skipped: {type(exc).__name__}: {str(exc)[:120]}"
            )
    out.findings = findings
    if r.coverage.stopped_reason:
        out.blockers.append(
            f"Crawl stopped early ({r.coverage.stopped_reason}); unchecked pages are not judged."
        )
    else:
        # Every technical rule ran on the whole crawl, so earlier tech:* findings
        # that are absent now count as rechecked; other checks name exact keys.
        out.checked.keys += ledger_keys(ctx, ("tech:",)) + [
            f.key for f in findings if f.key.startswith("tech:")
        ]
        out.checked.keys += site_crawl.checkable_keys(output, ctx.config)
        out.checked.keys += ymyl_keys + video_keys
        out.checked.keys = sorted(
            {blog_scoped(k, BLOG_CHECKS) for k in out.checked.keys}
        )
    out.pages = [p.final_url or p.url for p in r.pages if p.signals][:300]
    ctx.store.save_snapshot(
        ctx.config.id, "site-crawl", ctx.today, site_crawl.snapshot(output)
    )
    out.summary = (
        f"{r.coverage.pages_audited} pages audited, {len(output.rule_findings)} technical rule findings, "
        f"{len(findings)} findings in total (no paid calls)."
    )
    return out


async def cmd_videos(ctx: Context) -> Outcome:
    out = Outcome(
        skill=video_seo.SKILL,
        reason="Refresh the video catalog from the owner's playlists.",
        summary="",
    )
    playlists = [ctx.args.playlist] if ctx.args.playlist else ctx.config.video.playlists
    if not playlists:
        out.blockers.append(
            "No playlist: pass --playlist or set video.playlists in the site config."
        )
        return out
    catalog: dict[str, dict[str, Any]] = {
        v["id"]: v
        for v in ctx.store.read_json(ctx.config.id, "videos-catalog.json", [])
    }
    for url in playlists:
        try:
            for v in video_seo.read_playlist(url):
                catalog[v.id] = v.__dict__
        except Exception as exc:
            out.blockers.append(
                f"Playlist not read ({type(exc).__name__}: {str(exc)[:160]})"
            )
    ctx.store.write_json(ctx.config.id, "videos-catalog.json", list(catalog.values()))
    out.tools.append(
        ToolUse(
            name="yt-dlp (metadata only, no downloads)",
            kind="other",
            status="success" if catalog else "failed",
            calls=len(playlists),
        )
    )
    ctx.store.save_snapshot(
        ctx.config.id,
        "video-seo",
        ctx.today,
        {"catalog_size": len(catalog), "playlists": playlists},
    )
    out.summary = f"Video catalog has {len(catalog)} videos; run the crawl to check embeds and matches."
    return out


async def cmd_competitors(ctx: Context) -> Outcome:
    out = Outcome(
        skill=competitor_watch.SKILL,
        reason="Watch competitors' sitemaps for new and refreshed pages.",
        summary="",
    )
    previous = ctx.store.read_json(
        ctx.config.id, "competitor-sitemaps.json", {}, data=True
    )
    crawl_snap = ctx.store.latest(ctx.config.id, "site-crawl")
    own = [
        page_path(p["url"]) for p in (crawl_snap or {}).get("data", {}).get("pages", [])
    ]
    snaps, diffs = await competitor_watch.watch(ctx.config, previous, own)
    ctx.store.write_json(
        ctx.config.id,
        "competitor-sitemaps.json",
        {s.domain: s.urls for s in snaps if s.urls}
        | {
            k: v
            for k, v in previous.items()
            if k not in {s.domain for s in snaps if s.urls}
        },
        data=True,
    )
    out.tools.append(
        ToolUse(
            name="HTTP GET competitor robots.txt and sitemaps (UrlPolicy)",
            kind="aurora",
            status="success",
            calls=sum(s.sitemaps_read for s in snaps),
        )
    )
    out.findings = competitor_watch.watch_findings(diffs)
    out.checked.keys += [
        k
        for s in snaps
        if s.urls and s.domain in previous
        for k in (
            f"competitor-new-topics:{s.domain}",
            f"competitor-refresh-wave:{s.domain}",
        )
    ]
    for s in snaps:
        out.notes += [f"{s.domain}: {n}" for n in s.notes]
    first = [s.domain for s in snaps if s.domain not in previous]
    if first:
        out.notes.append(
            "First snapshot (baseline, no changes yet) for: " + ", ".join(first)
        )
    ctx.store.save_snapshot(
        ctx.config.id,
        "competitor-watch",
        ctx.today,
        competitor_watch.snapshot(snaps, diffs),
    )
    out.sections.append(
        ReportSection(
            title="Competitor changes since the last run",
            markdown=md_table(
                [
                    "Competitor",
                    "URLs",
                    "New",
                    "Updated",
                    "Removed",
                    "New on your keywords",
                    "Not covered by you",
                ],
                [
                    [
                        d.domain,
                        len(s.urls),
                        len(d.new),
                        len(d.updated),
                        len(d.removed),
                        len(d.topic_matches),
                        len(d.uncovered),
                    ]
                    for s, d in zip(snaps, diffs, strict=True)
                ],
            ),
        )
    )
    out.summary = f"{len(snaps)} competitors read; {sum(len(d.new) for d in diffs)} new and {sum(len(d.updated) for d in diffs)} updated URLs."
    return out


# --- Execution wrapper -------------------------------------------------------------------

MODES = {
    "opportunities": ("opportunities", cmd_opportunities),
    "alerts": ("alerts", cmd_alerts),
    "ranks": ("rank-tracking", cmd_ranks),
    "keyword-data": ("rank-tracking", cmd_keyword_data),
    "ai-visibility": ("ai-visibility", cmd_ai_visibility),
    "crawl": ("site-crawl", cmd_crawl),
    "videos": ("video-seo", cmd_videos),
    "competitors": ("competitor-watch", cmd_competitors),
}


def payload_of(out: Outcome) -> ExecutionPayload:
    return ExecutionPayload(
        summary=out.summary or None,
        pages_analysed=out.pages,
        skills_used=[
            SkillUse(
                name=out.skill,
                reason=out.reason,
                analysed=out.summary,
                actions=out.actions,
                output=f"{len(out.findings)} findings",
            )
        ],
        tools_used=out.tools,
        findings=out.findings,
        checked=out.checked,
        blockers=out.blockers,
        notes=out.notes,
        sections=out.sections,
    )


async def run_mode(ctx: Context, command: str) -> int:
    mode, func = MODES[command]
    agent = ctx.agent
    execution_id = ctx.args.execution
    own = execution_id is None
    if own:
        execution_id = agent.start(ctx.config.id, mode).id  # type: ignore[arg-type]
    try:
        out = await func(ctx)
    except Exception as exc:
        if own:
            agent.record(
                ctx.config.id,
                execution_id,
                ExecutionPayload(
                    blockers=[
                        f"{command} failed: {type(exc).__name__}: {str(exc)[:300]}"
                    ]
                ),
            )
            agent.finish(ctx.config.id, execution_id, "failed")
        raise
    agent.record(ctx.config.id, execution_id, payload_of(out))
    result: dict[str, Any] = {
        "execution": execution_id,
        "summary": out.summary,
        "findings": len(out.findings),
        "blockers": out.blockers,
        "notes": out.notes,
        "pdf": out.pdf,
    }
    if own:
        status = "partial" if out.blockers else "completed"
        if out.blockers and not out.findings and not out.sections:
            status = "blocked"
        execution, _ = agent.finish(ctx.config.id, execution_id, status)
        result["status"] = execution.status
        result["comparison"] = (
            execution.comparison.model_dump() if execution.comparison else None
        )
        result["report"] = str(
            agent.repository.site_dir(ctx.config.id) / "reports" / f"{execution_id}.md"
        )
    print(json.dumps(result, indent=2, default=str))
    return 0


# --- Parser -----------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", required=True)

    def add(name: str) -> argparse.ArgumentParser:
        p = sub.add_parser(name)
        p.add_argument("site")
        if name in MODES:
            p.add_argument(
                "--execution",
                help="record into this running execution instead of starting one",
            )
        return p

    add("opportunities").add_argument(
        "--conversions", help="CSV export: page + conversions/key events/leads"
    )
    p = add("alerts")
    p.add_argument(
        "--pdf", action="store_true", help="write a PDF when there are alerts"
    )
    p.add_argument(
        "--no-inspect", action="store_true", help="skip URL Inspection of key pages"
    )
    p = add("ranks")
    p.add_argument(
        "--serp", action="store_true", help="also run budgeted SerpApi checks"
    )
    p.add_argument("--max-searches", type=int, default=0)
    p.add_argument(
        "--force", action="store_true", help="run SERP checks even if not due"
    )
    p = add("keyword-data")
    p.add_argument(
        "--keywords",
        help="comma-separated keywords (default: tracked, seed and top queries)",
    )
    p.add_argument(
        "--backlinks", action="store_true", help="DataForSEO backlink summaries (paid)"
    )
    add("ai-visibility")
    p = add("record-ai-answer")
    p.add_argument("--prompt", required=True)
    p.add_argument("--engine", required=True, choices=ai_visibility.ENGINES)
    p.add_argument("--date", required=True, help="YYYY-MM-DD the answer was observed")
    p.add_argument("--cited", default="", help="comma-separated cited domains")
    p.add_argument("--mention", dest="mention", action="store_true")
    p.add_argument("--no-mention", dest="mention", action="store_false")
    p.set_defaults(mention=False)
    p.add_argument("--excerpt", default="")
    p.add_argument("--observed-by", default="")
    p = add("crawl")
    p.add_argument("--max-pages", type=int, default=150)
    p.add_argument("--no-ymyl", action="store_true")
    p.add_argument("--no-video", action="store_true")
    p.add_argument("--no-source-map", action="store_true")
    add("videos").add_argument("--playlist")
    add("competitors")
    add("export")
    add("dashboard")
    p = add("rollback-plan")
    p.add_argument("ref", help="commit reference or finding key of a recorded change")
    p = add("rollback-prepare")
    p.add_argument("ref")
    p.add_argument("--approved-by", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = get_settings()
    agent = build_seo_agent_service(settings)
    try:
        config = agent.website(args.site)
        ctx = Context(
            settings, agent, build_insight_store(settings), config, date.today(), args
        )
        if args.command in MODES:
            return asyncio.run(run_mode(ctx, args.command))
        if args.command == "record-ai-answer":
            return cmd_record_answer(ctx)
        findings = agent.repository.load_findings(config.id)
        if args.command == "export":
            paths = exports.export_csv(ctx.store, config.id, findings)
            print(json.dumps({"files": [str(p) for p in paths]}, indent=2))
            return 0
        if args.command == "dashboard":
            print(
                json.dumps(
                    exports.dashboard(ctx.store, config.id, findings),
                    indent=2,
                    default=str,
                )
            )
            return 0
        repo = Path(config.repository.path or "")
        if not repo.is_dir():
            print(json.dumps({"error": "No website repository configured."}))
            return 2
        changes = agent.repository.load_changes(config.id)
        plan = rollback.plan(repo, changes, args.ref)
        if args.command == "rollback-plan":
            print(json.dumps(plan.__dict__, indent=2))
            return 0
        branch, staged = rollback.prepare(repo, plan, args.approved_by)
        print(
            json.dumps(
                {
                    "branch": branch,
                    "staged": staged,
                    "next": "Review with 'git diff --cached'; commit it yourself or run 'git revert --abort'. Nothing was committed or pushed.",
                },
                indent=2,
            )
        )
        return 0
    except (AuroraException, rollback.RollbackError, ValueError) as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
