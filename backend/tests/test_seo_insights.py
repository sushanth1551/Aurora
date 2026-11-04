"""SEO Intelligence insight modules: opportunities, alerts, rank tracking, keyword data,
AI visibility, YMYL, video, crawl extras, competitor watch, source map, rollback,
store/exports, blog aggregation and the DataForSEO adapter (all offline)."""

from __future__ import annotations

import asyncio
import json
import subprocess
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
import respx
from pydantic import SecretStr, ValidationError

from app.core.config import Settings
from app.core.exceptions import ConfigurationException
from app.seo.intelligence.insights import (
    ai_visibility,
    alerts,
    competitor_watch,
    keyword_data,
    market_ranks,
    opportunities,
    rollback,
    site_crawl,
    source_map,
    video_seo,
    ymyl,
)
from app.seo.intelligence.insights.common import (
    GscRow,
    aggregate_blog,
    blog_scoped,
    rows_of,
)
from app.seo.intelligence.insights.exports import dashboard, export_csv
from app.seo.intelligence.insights.store import InsightStore
from app.seo.intelligence.knowledge import SeoKnowledgeRepository
from app.seo.intelligence.models import (
    FINDING_KEY_RE,
    AlertSettings,
    ChangeRecord,
    Fact,
    KeywordDataSettings,
    RankTracking,
    WebsiteConfig,
    YmylSettings,
)
from app.tools.dataforseo_tool import BASE_URL, DataForSeoTool

BASE = "https://example.com"
TODAY = date(2026, 11, 4)


def qp(
    query: str, page: str, clicks: float, impressions: float, position: float
) -> GscRow:
    return GscRow({"query": query, "page": BASE + page}, clicks, impressions, position)


def dp(
    day: date, page: str, clicks: float, impressions: float = 100, position: float = 5
) -> GscRow:
    return GscRow(
        {"date": day.isoformat(), "page": BASE + page}, clicks, impressions, position
    )


def config(**extra: object) -> WebsiteConfig:
    return WebsiteConfig(
        id="example", name="Example", domain="example.com", base_url=BASE, **extra
    )


def assert_keys_valid(findings: list) -> None:
    for f in findings:
        assert FINDING_KEY_RE.match(f.key), f.key
        assert ":" in f.key


# --- common ------------------------------------------------------------------------------


def test_rows_of_skips_malformed_rows() -> None:
    rows = rows_of(
        [
            {
                "keys": ["q", "https://example.com/a"],
                "clicks": 2,
                "impressions": 10,
                "position": 3,
            },
            {"keys": ["only-one"], "clicks": 1},
            {"keys": ["q", "p"], "clicks": "x"},
        ],
        ("query", "page"),
    )
    assert len(rows) == 1 and rows[0].ctr == 0.2


def test_blog_aggregation_merges_posts_and_keeps_key_pages() -> None:
    findings = ymyl.accuracy_findings(
        [
            ymyl.PageAccuracy(url=BASE + "/blog/a", unsourced=["$1,000 fee"]),
            ymyl.PageAccuracy(
                url=BASE + "/blog/b", unsourced=["75% pass rate", "x", "y"]
            ),
            ymyl.PageAccuracy(url=BASE + "/pricing", unsourced=["$10"]),
        ],
        YmylSettings(),
        TODAY,
    )
    merged = aggregate_blog(
        findings, ("unsourced-numbers",), {"unsourced-numbers": "Unsourced"}
    )
    keys = sorted(f.key for f in merged)
    assert keys == ["unsourced-numbers:/pricing", "unsourced-numbers:blog"]
    blog = next(f for f in merged if f.key == "unsourced-numbers:blog")
    assert len(blog.affected_urls) == 2 and blog.priority == "high"
    assert (
        blog_scoped("unsourced-numbers:/blog/a", ("unsourced-numbers",))
        == "unsourced-numbers:blog"
    )
    assert (
        blog_scoped("tech:title.length", ("unsourced-numbers",)) == "tech:title.length"
    )


# --- opportunities ---------------------------------------------------------------------------


def curve_rows() -> list[GscRow]:
    return [
        qp("a", "/x", 300, 1000, 1.0),
        qp("b", "/x", 200, 1000, 2.0),
        qp("c", "/x", 100, 1000, 3.0),
        qp("d", "/x", 30, 1000, 8.0),
    ]


def test_ctr_curve_uses_only_well_measured_positions() -> None:
    curve = opportunities.ctr_curve(curve_rows() + [qp("e", "/y", 5, 50, 12)])
    assert curve[3] == pytest.approx(0.1) and 12 not in curve


def test_striking_distance_estimates_from_own_curve() -> None:
    rows = curve_rows() + [qp("usmle fees", "/blog/fees", 10, 1000, 7.0)]
    curve = opportunities.ctr_curve(rows)
    opps = opportunities.striking_distance(rows, curve)
    fees = next(o for o in opps if o.query == "usmle fees")
    assert fees.reference_ctr == pytest.approx(0.1)
    assert fees.extra_clicks == pytest.approx(90.0)


def test_striking_distance_without_curve_does_not_estimate() -> None:
    opps = opportunities.striking_distance([qp("q", "/p", 1, 150, 9)], {})
    assert opps[0].extra_clicks is None


def test_low_ctr_flags_good_position_with_poor_ctr() -> None:
    rows = curve_rows() + [qp("snippet", "/blog/s", 5, 1000, 2.0)]
    curve = opportunities.ctr_curve(rows)
    found = opportunities.low_ctr(rows, curve)
    assert [o.query for o in found] == ["snippet"]
    findings = opportunities.opportunity_findings(found, BASE)
    assert findings[0].key == "low-ctr:/blog/s"
    assert_keys_valid(findings)


def test_page_trends_decay_dead_and_merge() -> None:
    end = TODAY
    rows: list[GscRow] = []
    for i in range(180):
        day = end - timedelta(days=i)
        old = i >= 28
        rows.append(dp(day, "/blog/decay", 5 if old else 1))
        rows.append(dp(day, "/blog/strong", 10))
        if i >= 120:
            rows.append(dp(day, "/blog/dead", 1))
    queries = [
        qp("q1", "/blog/decay", 1, 100, 6),
        qp("q1", "/blog/strong", 5, 100, 4),
        qp("q2", "/blog/decay", 1, 100, 6),
        qp("q2", "/blog/strong", 5, 100, 4),
    ]
    trends = {
        t.page: t for t in opportunities.page_trends(rows, end, queries_recent=queries)
    }
    assert trends["/blog/decay"].status == "decaying"
    assert (
        trends["/blog/decay"].decision == "merge"
        and trends["/blog/decay"].merge_with == "/blog/strong"
    )
    assert trends["/blog/dead"].status == "dead"
    assert trends["/blog/strong"].status == "stable"
    keys = {f.key for f in opportunities.trend_findings(list(trends.values()), BASE)}
    assert keys == {"content-decay:/blog/decay", "prune-review:/blog/dead"}


def test_conversions_import_and_lead_value() -> None:
    text = "# GA4 export\nLanding page,Sessions,Key events\n/blog/fees,100,4\n(not set),5,1\nTotal,105,5\n"
    conv = opportunities.load_conversions(text)
    assert conv == {"/blog/fees": 4.0}
    opps = [
        opportunities.Opportunity(
            "striking-distance", "q", "/blog/fees", 1000, 10, 7, 0.01, 0.1, 90.0
        )
    ]
    opportunities.add_leads(opps, opportunities.page_values(conv, {"/blog/fees": 40}))
    assert opps[0].extra_leads == pytest.approx(9.0)
    with pytest.raises(ValueError):
        opportunities.load_conversions("a,b\n1,2\n")


# --- alerts ----------------------------------------------------------------------------------


def daily_site(values: list[float], end: date) -> list[GscRow]:
    rows = []
    for week, total in enumerate(values):
        for d in range(7):
            day = end - timedelta(days=(len(values) - 1 - week) * 7 + (6 - d))
            rows.append(GscRow({"date": day.isoformat()}, total / 7, total * 20 / 7, 5))
    return rows


def test_robust_drop_alerts_only_on_real_drops() -> None:
    settings = AlertSettings()
    noisy = daily_site([100, 110, 95, 105, 98, 102, 107, 93, 96], TODAY)
    assert alerts.detect(noisy, [], [], TODAY, settings) == []
    dropped = daily_site([100, 110, 95, 105, 98, 102, 107, 93, 40], TODAY)
    found = alerts.detect(dropped, [], [], TODAY, settings)
    assert {a.kind for a in found} >= {"site-clicks"}
    assert alerts.alert_key(found[0]) in ("traffic-drop:site", "impressions-drop:site")


def test_flat_baseline_uses_percentage_only() -> None:
    _, change, z = alerts.robust_drop([50] * 8 + [20])
    assert z is None and change == pytest.approx(-60)


def test_index_alerts_and_keys() -> None:
    found = alerts.index_alerts(
        {
            BASE + "/": {"coverageState": "Submitted and indexed", "verdict": "PASS"},
            BASE + "/a": {
                "coverageState": "Crawled - currently not indexed",
                "verdict": "NEUTRAL",
            },
            BASE + "/b": {
                "coverageState": "Submitted and indexed",
                "verdict": "PASS",
                "googleCanonical": BASE + "/c",
                "userCanonical": BASE + "/b",
            },
        }
    )
    assert sorted(alerts.alert_key(a) for a in found) == [
        "index-status:/a",
        "index-status:/b",
    ]
    assert_keys_valid(alerts.alert_findings(found, BASE))


def test_position_drops() -> None:
    rows = []
    for i in range(63):
        day = TODAY - timedelta(days=i)
        rows.append(
            GscRow(
                {"date": day.isoformat(), "query": "usmle"}, 1, 20, 4 if i >= 7 else 9
            )
        )
    found = alerts.position_drops(rows, TODAY)
    assert found and found[0].subject == "usmle"


# --- rank tracking -----------------------------------------------------------------------------


def test_market_ranks_with_history_and_drop_finding() -> None:
    rows = [
        GscRow({"query": "USMLE Fees", "country": "ind"}, 2, 100, 9.0),
        GscRow({"query": "usmle fees", "country": "usa"}, 1, 50, 4.0),
        GscRow({"query": "other", "country": "ind"}, 1, 50, 2.0),
    ]
    history = [
        {
            "period_end": "2026-10-20",
            "keyword": "usmle fees",
            "market": "IN",
            "position": 4.0,
        }
    ]
    ranks = market_ranks.gsc_market_ranks(
        rows, ["usmle fees"], ["IN", "US"], TODAY, history
    )
    india = next(r for r in ranks if r.market == "IN")
    assert india.position == 9.0 and india.movement == -5.0
    findings = market_ranks.rank_findings(ranks, [], BASE)
    assert [f.key for f in findings] == ["keyword-ranking-drop:usmle-fees:in"]


class FakeSearcher:
    def __init__(self, fail: bool = False) -> None:
        self.calls = 0
        self.fail = fail

    async def search_serp(self, query: str, **kwargs: object) -> SimpleNamespace:
        self.calls += 1
        if self.fail:
            raise RuntimeError("boom")
        organic = [
            SimpleNamespace(position=1, domain="rival.com", url="https://rival.com/a"),
            SimpleNamespace(position=3, domain="www.example.com", url=BASE + "/fees"),
        ]
        features = SimpleNamespace(
            featured_snippet=SimpleNamespace(domain="rival.com"),
            ai_overview=SimpleNamespace(status="present", cited_domains=["rival.com"]),
            people_also_ask=[SimpleNamespace(question="How much?")],
        )
        return SimpleNamespace(organic=organic, features=features)


def test_serp_checks_respect_budget_and_never_retry() -> None:
    settings = RankTracking(
        markets=["IN", "US"], devices=["mobile"], serpapi_monthly_budget=10
    )
    searcher = FakeSearcher()
    checks, spent, notes = asyncio.run(
        market_ranks.serp_checks(
            searcher, ["usmle fees", "step 1"], settings, "example.com", "en", TODAY, 3
        )
    )
    assert spent == 3 and searcher.calls == 3 and len(checks) == 3 and notes
    c = checks[0]
    assert (
        c.our_position == 3
        and c.featured_snippet_domain == "rival.com"
        and c.ai_overview_cites_site is False
    )
    failing = FakeSearcher(fail=True)
    checks, spent, notes = asyncio.run(
        market_ranks.serp_checks(
            failing, ["a"], settings, "example.com", "en", TODAY, 5
        )
    )
    assert failing.calls == 2 and checks == [] and len(notes) == 2
    assert market_ranks.budget_left({"2026-11": 8}, settings, TODAY) == 2
    keys = {f.key for f in market_ranks.rank_findings([], [c], BASE)}
    assert keys == {"featured-snippet-lost:usmle-fees:in"}
    assert {f.key for f in ai_visibility.overview_findings([c])} == {
        "ai-overview-not-cited:usmle-fees:in"
    }


# --- keyword data ------------------------------------------------------------------------------


class FakeProvider:
    def __init__(self) -> None:
        self.cost_usd = 0.0

    async def search_volume(
        self, keywords: list[str], location_code: int, language_code: str = "en"
    ) -> list[dict]:
        self.cost_usd += 0.05
        return [{"keyword": k, "search_volume": 1500, "cpc": 1.2} for k in keywords]

    async def keyword_difficulty(
        self, keywords: list[str], location_code: int, language_code: str = "en"
    ) -> list[dict]:
        self.cost_usd += 0.05
        return [{"keyword": k, "keyword_difficulty": 20} for k in keywords]


def test_keyword_enrichment_within_budget_and_gap_findings() -> None:
    rows = keyword_data.gsc_demand(
        [GscRow({"query": "usmle fees", "country": "ind"}, 1, 30, 25.0)],
        ["usmle fees"],
        ["IN"],
    )
    provider = FakeProvider()
    spent, notes = asyncio.run(
        keyword_data.enrich(
            provider,
            rows,
            KeywordDataSettings(provider="dataforseo", monthly_budget_usd=0.05),
            "en",
            0.0,
        )
    )
    assert spent == pytest.approx(0.05) and rows[0].search_volume == 1500
    assert rows[0].keyword_difficulty is None and any("budget" in n for n in notes)
    rows[0].keyword_difficulty = 20
    findings = keyword_data.keyword_findings(rows, BASE)
    assert [f.key for f in findings] == ["keyword-gap:usmle-fees:in"] and findings[
        0
    ].priority == "high"


def test_backlink_gap_finding() -> None:
    found = keyword_data.backlink_findings(
        {
            "example.com": {"referring_domains": 50},
            "rival.com": {"referring_domains": 400},
        },
        "example.com",
    )
    assert [f.key for f in found] == ["referring-domain-gap:site"]


@respx.mock
def test_dataforseo_adapter_reports_cost() -> None:
    respx.post(BASE_URL + "/v3/keywords_data/google_ads/search_volume/live").mock(
        return_value=httpx.Response(
            200,
            json={
                "status_code": 20000,
                "cost": 0.075,
                "tasks": [
                    {
                        "status_code": 20000,
                        "result": [{"keyword": "a", "search_volume": 90}],
                    }
                ],
            },
        )
    )
    tool = DataForSeoTool(
        Settings(dataforseo_login="me", dataforseo_password=SecretStr("pw"))
    )
    rows = asyncio.run(tool.search_volume(["a"], 2356))
    assert rows[0]["search_volume"] == 90 and tool.cost_usd == pytest.approx(0.075)
    request = respx.calls.last.request
    assert request.headers["authorization"].startswith("Basic ")


def test_dataforseo_requires_credentials() -> None:
    with pytest.raises(ConfigurationException):
        asyncio.run(DataForSeoTool(Settings()).search_volume(["a"], 2356))


# --- AI visibility ------------------------------------------------------------------------------


def test_ai_crawler_access_by_purpose_and_explicit_policy() -> None:
    robots = "User-agent: OAI-SearchBot\nDisallow: /\n\nUser-agent: GPTBot\nDisallow: /blog/*\n\nUser-agent: *\nAllow: /\n"
    access = {
        a.agent: a for a in ai_visibility.crawler_access(robots, BASE, ["/", "/blog/x"])
    }
    assert access["OAI-SearchBot"].blocked_paths == ["/", "/blog/x"]
    assert access["GPTBot"].blocked_paths == ["/blog/x"]
    assert not access["PerplexityBot"].blocked
    assert ai_visibility.explicit_ai_agents(robots) == ["GPTBot", "OAI-SearchBot"]
    keys = {
        f.key
        for f in ai_visibility.access_findings(
            list(access.values()),
            ai_visibility.AiVisibilitySettings(allow_training_crawlers=True),
            BASE,
            [],
        )
    }
    assert keys == {
        "robots-ai-crawlers:site",
        "ai-answer-crawlers-blocked:site",
        "ai-training-crawler-policy:site",
    }


def test_llms_txt_and_prompt_panel() -> None:
    assert ai_visibility.check_llms_txt(
        200, "# Site\n\n## Docs\n- [A](https://example.com/a)\n"
    ).valid
    assert not ai_visibility.check_llms_txt(404, None).valid
    answers = [
        ai_visibility.AiAnswer(
            "best usmle coaching", "perplexity", "2026-11-01", ["rival.com"], False
        ),
        ai_visibility.AiAnswer(
            "best usmle coaching", "perplexity", "2026-11-03", ["www.example.com"], True
        ),
        ai_visibility.AiAnswer(
            "usmle fees", "chatgpt", "2026-11-03", ["rival.com"], False
        ),
    ]
    sov = ai_visibility.share_of_voice(answers, "example.com")
    assert sov["engines"]["perplexity"]["share_cited_pct"] == 100.0
    assert sov["engines"]["chatgpt"]["cited"] == 0
    keys = [
        f.key for f in ai_visibility.panel_findings(answers, "example.com", "Example")
    ]
    assert keys == ["ai-prompt-gap:usmle-fees"]
    with pytest.raises(ValueError):
        ai_visibility.AiAnswer("q", "bing-chat", "2026-11-03", [], False)


# --- YMYL ------------------------------------------------------------------------------------------


PAGE = """<html><head><title>USMLE Step 1 Fees (2024 Guide)</title></head><body>
<h1>USMLE Step 1 fees</h1>
<p>The fee is $1,000 according to <a href="https://www.usmle.org/fees">USMLE</a>.</p>
<h2>What students pay</h2>
<p>The Step 1 fee is $1,000 for every student this year.</p>
<a href="/blog/x"><p>Teaser: 95% pass rate for our students in this guide</p></a>
<p>No score guarantees a match, and program directors weigh more.</p>
<p>We are the #1 USMLE coaching for Indian students.</p>
</body></html>"""


def test_ymyl_page_checks() -> None:
    settings = YmylSettings(
        facts=[
            Fact(
                id="step1-fee",
                statement="Step 1 registration fee",
                pattern=r"Step 1 fee is (\$[\d,]+)",
                expected="$1,035",
                source_url="https://www.usmle.org/fees",
                verified_on=date(2026, 10, 1),
            )
        ]
    )
    result = ymyl.check_page(BASE + "/blog/fees", PAGE, settings, TODAY)
    assert len(result.unsourced) == 1 and "every student" in result.unsourced[0]
    assert any("2024" in s for s in result.stale)
    assert set(result.claims) == {
        "#1"
    }  # 'guarantees' is negated, the teaser is skipped
    assert result.fact_mismatches == [("step1-fee", "$1,000")]
    assert result.page_has_official_link
    keys = {f.key for f in ymyl.accuracy_findings([result], settings, TODAY)}
    assert keys == {
        "unsourced-numbers:/blog/fees",
        "stale-year:/blog/fees",
        "unsupported-claim:/blog/fees",
        "fact-mismatch:step1-fee:/blog/fees",
    }


def test_ymyl_listing_pages_skip_heading_years_and_due_facts() -> None:
    html = "<html><body><h2>Important update in 2024</h2></body></html>"
    assert ymyl.check_page(BASE + "/blog", html, YmylSettings(), TODAY).stale == []
    old = Fact(
        id="f",
        statement="s",
        pattern=r"x (\d+)",
        expected="1",
        source_url="https://usmle.org",
        verified_on=date(2025, 1, 1),
        review_after_days=180,
    )
    assert ymyl.due_facts(YmylSettings(facts=[old]), TODAY) == ["f"]
    with pytest.raises(ValidationError):
        Fact(
            id="g",
            statement="s",
            pattern="no group",
            expected="1",
            source_url="u",
            verified_on=TODAY,
        )


# --- Video ------------------------------------------------------------------------------------------


def test_video_embeds_schema_and_matches() -> None:
    html_no_schema = '<iframe src="https://www.youtube.com/embed/ABCDEFGHIJK"></iframe>'
    html_schema = (
        html_no_schema
        + '<script type="application/ld+json">{"@type": "VideoObject"}</script>'
    )
    assert video_seo.embedded_ids(html_no_schema) == ["ABCDEFGHIJK"]
    assert video_seo.iso_duration(3725) == "PT1H2M5S"
    catalog = [
        video_seo.Video(
            id="ABCDEFGHIJK",
            title="Step 1 study plan for IMGs",
            upload_date="2026-01-02",
            duration_seconds=90,
        )
    ]
    obj = video_seo.video_object(catalog[0])
    assert (
        obj["@type"] == "VideoObject"
        and obj["duration"] == "PT1M30S"
        and obj["uploadDate"] == "2026-01-02"
    )
    findings, details = video_seo.video_findings(
        {BASE + "/blog/a": html_no_schema, BASE + "/blog/b": html_schema},
        catalog,
        {BASE + "/blog/c": "Step 1 study plan for Indian IMGs"},
    )
    keys = {f.key for f in findings}
    assert keys == {"videoobject-missing:/blog/a", "video-embed-opportunities:site"}
    assert list(details["videoobject_proposals"]) == [BASE + "/blog/a"]


# --- Crawl extras ---------------------------------------------------------------------------------


def test_crawl_extras_and_checkable_keys() -> None:
    page = SimpleNamespace(
        final_url=BASE + "/blog/thin",
        url=BASE + "/blog/thin",
        signals=SimpleNamespace(word_count=300),
    )
    output = site_crawl.CrawlOutput(
        result=SimpleNamespace(pages=[page]),
        rule_findings=[],
        html={
            BASE + "/": '<img src="a.png"><img src="b.png" width="10" height="10">',
            BASE + "/blog/thin": "<p>x</p>",
        },
        headers={
            BASE + "/": {
                "content-type": "text/html",
                "x-content-type-options": "nosniff",
            }
        },
        soft_404_status=200,
    )
    cfg = config()
    cfg.rules.min_words_blog_post = 800
    keys = {f.key for f in site_crawl.extra_findings(output, cfg)}
    assert keys == {
        "soft-404:site",
        "image-dimensions-missing:/",
        "security-headers-missing:site",
        "thin-content:/blog/thin",
    }
    checkable = site_crawl.checkable_keys(output, cfg)
    assert "thin-content:/blog/thin" in checkable and "soft-404:site" in checkable


# --- Competitor watch ------------------------------------------------------------------------------


def test_competitor_diff_and_topic_classification() -> None:
    snap = competitor_watch.CompetitorSnapshot(
        domain="rival.com",
        urls={
            "https://rival.com/blog/usmle-step-1-fees-2026": "2026-11-01",
            "https://rival.com/old": "2026-11-02",
            "https://rival.com/a": "",
        },
        sitemaps_read=1,
    )
    assert competitor_watch.diff(None, snap).new == []  # first run is a baseline
    d = competitor_watch.diff(
        {"https://rival.com/old": "2026-10-01", "https://rival.com/gone": ""}, snap
    )
    assert d.new == [
        "https://rival.com/a",
        "https://rival.com/blog/usmle-step-1-fees-2026",
    ]
    assert d.removed == ["https://rival.com/gone"] and d.updated == [
        "https://rival.com/old"
    ]
    competitor_watch.classify(
        d, config(seed_keywords=["step 1 fees"]), ["/blog/how-to-apply"]
    )
    assert d.uncovered == ["https://rival.com/blog/usmle-step-1-fees-2026"]
    assert [f.key for f in competitor_watch.watch_findings([d])] == [
        "competitor-new-topics:rival.com"
    ]


# --- Source map and rollback (temporary git repositories) -------------------------------------------


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
    ).stdout


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    git(tmp_path, "init", "-q", "-b", "main")
    git(tmp_path, "config", "user.email", "t@example.com")
    git(tmp_path, "config", "user.name", "Test")
    (tmp_path / "package.json").write_text(
        json.dumps({"dependencies": {"react": "^18", "react-helmet-async": "^3"}})
    )
    (tmp_path / "posts.ts").write_text(
        'export const posts = [\n  { slug: "usmle-fees-guide", title: "USMLE Fees Guide 2026" },\n];\n'
    )
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-q", "-m", "init")
    return tmp_path


def test_source_map_locates_titles_and_framework(repo: Path) -> None:
    index = source_map.build_index(repo)
    assert any("react-helmet-async" in n for n in index.framework)
    assert source_map.locate(index, "USMLE Fees Guide 2026")[0][:2] == ("posts.ts", 2)
    findings = ymyl.accuracy_findings(
        [ymyl.PageAccuracy(url=BASE + "/blog/usmle-fees-guide", stale=["title: x"])],
        YmylSettings(),
        TODAY,
    )
    strings = source_map.page_strings(
        {BASE + "/blog/usmle-fees-guide": ("USMLE Fees Guide 2026", None)}
    )
    assert source_map.attach(findings, index, strings) == 1
    assert "posts.ts:2" in findings[0].evidence[-1].observation


def test_rollback_plan_and_prepare_are_gated(repo: Path) -> None:
    git(repo, "switch", "-q", "-c", "work")
    (repo / "posts.ts").write_text("changed\n")
    git(repo, "commit", "-qam", "seo: new title")
    sha = git(repo, "rev-parse", "--short", "HEAD").strip()
    changes = [
        ChangeRecord(
            description="new title",
            kind="seo_change",
            reference=sha,
            finding_keys=["long-titles:blog"],
        )
    ]
    plan = rollback.plan(repo, changes, "long-titles:blog")
    assert plan.files == ["posts.ts"] and plan.current_branch == "work"
    with pytest.raises(rollback.RollbackError):
        rollback.prepare(repo, plan, " ")
    (repo / "dirty.txt").write_text("x")
    with pytest.raises(rollback.RollbackError):
        rollback.prepare(repo, plan, "Owner")
    (repo / "dirty.txt").unlink()
    branch, staged = rollback.prepare(repo, plan, "Owner")
    assert branch.startswith("seo-rollback/") and "posts.ts" in staged
    assert (
        git(repo, "log", "-1", "--format=%s").strip() == "seo: new title"
    )  # nothing committed
    with pytest.raises(rollback.RollbackError):
        rollback.find_change(changes, "nomatch")


# --- Store and exports ------------------------------------------------------------------------------


def test_store_snapshots_exports_and_dashboard(tmp_path: Path) -> None:
    repo = SeoKnowledgeRepository(tmp_path / "kb")
    store = InsightStore(repo, tmp_path / "data")
    store.save_snapshot(
        "example",
        "alerts",
        TODAY,
        {
            "alerts": [
                {
                    "kind": "site-clicks",
                    "subject": "whole site",
                    "detail": "d",
                    "priority": "high",
                }
            ]
        },
    )
    store.save_snapshot(
        "example",
        "rank-tracking",
        TODAY,
        {
            "market_ranks": [{"keyword": "k", "market": "IN", "position": 4.0}],
            "serp_checks": [],
        },
    )
    assert store.latest("example", "alerts")["date"] == TODAY.isoformat()
    findings = alerts.alert_findings(
        [alerts.Alert("site-clicks", "whole site", 1, 2, -50.0, -4.0, "d", "high")],
        BASE,
    )
    paths = export_csv(store, "example", findings)
    names = sorted(p.name for p in paths)
    assert names == [
        "example-alerts.csv",
        "example-findings.csv",
        "example-market-ranks.csv",
    ]
    assert paths[0].read_text(encoding="utf-8").startswith("﻿")
    board = dashboard(store, "example", findings)
    assert board["open_findings"]["by_priority"]["high"] == 1
    assert (
        board["updated"]["alerts"] == TODAY.isoformat()
        and board["updated"]["opportunities"] is None
    )
    with pytest.raises(ValueError):
        store.save_snapshot("example", "unknown", TODAY, {})


# --- Config and registry ------------------------------------------------------------------------------


def test_new_config_sections_have_safe_defaults() -> None:
    cfg = config()
    assert cfg.rank_tracking.serpapi_monthly_budget == 0
    assert cfg.keyword_data.provider == "none"
    assert cfg.ymyl.facts == []
    with pytest.raises(ValidationError):
        RankTracking(markets=["India"])


def test_committed_site_config_and_registry_load() -> None:
    from app.seo.intelligence.factory import build_registry

    root = Path(__file__).resolve().parents[2]
    SeoKnowledgeRepository(root / "seo-intelligence").load_config("nextsteps")
    registry = build_registry(Settings())
    steps = registry.plan("audit")
    assert {s.key for s in steps} >= {
        "site-crawl",
        "ymyl",
        "opportunities",
        "ai-visibility",
    }
    assert {s.group for s in steps if s.key in ("technical", "site-crawl")} == {
        "checks"
    }
    for mode in (
        "opportunities",
        "alerts",
        "rank-tracking",
        "ai-visibility",
        "site-crawl",
        "competitor-watch",
        "video-seo",
    ):
        assert all(s.availability != "missing_skill" for s in registry.plan(mode)), mode


def test_never_shown_pages_need_an_index_check_and_low_ctr_scope() -> None:
    end = TODAY
    rows = [dp(end - timedelta(days=i), "/blog/live", 2) for i in range(180)]
    trends = {
        t.page: t
        for t in opportunities.page_trends(rows, end, known_pages=["/blog/new"])
    }
    assert trends["/blog/new"].status == "no-impressions"
    assert trends["/blog/new"].decision == "index-check"
    keys = {f.key for f in opportunities.trend_findings(list(trends.values()), BASE)}
    assert keys == {"no-impressions:/blog/new"}
    scope = opportunities.low_ctr_scope(
        [qp("a", "/top", 1, 500, 3.0), qp("b", "/deep", 1, 500, 8.6)]
    )
    assert scope == {"/top"}
