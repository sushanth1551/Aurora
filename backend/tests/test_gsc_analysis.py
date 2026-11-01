"""Search Console detectors and recommendations on synthetic aggregates (pure, offline)."""

from datetime import date

import pytest

from app.monitoring.analysis import (
    article_data_state,
    bucket,
    ctr_baselines,
    decline_driver,
    find_cannibalization,
    find_declining_clicks,
    find_growing,
    find_low_ctr,
    find_new_queries,
    find_position_decline,
)
from app.monitoring.models import Agg, Thresholds, Window
from app.monitoring.recommendations import (
    build_insights,
    impact_from_share,
    no_data_insight,
)

T = Thresholds()


def agg(page=None, query=None, clicks=0.0, impressions=0.0, position=None) -> Agg:
    return Agg(
        page=page,
        query=query,
        clicks=clicks,
        impressions=impressions,
        position_weighted=(position or 0.0) * impressions,
    )


def window(current_days=28, previous_days=28) -> Window:
    return Window(
        current_start=date(2026, 10, 4),
        current_end=date(2026, 10, 31),
        previous_start=date(2026, 9, 6),
        previous_end=date(2026, 10, 3),
        days=28,
        current_days_with_data=current_days,
        previous_days_with_data=previous_days,
        history_start=date(2026, 8, 1),
    )


# -- CTR baseline and low CTR ---------------------------------------------------------------


def healthy_rows(n=30, position=3.0):
    return [
        agg(
            page=f"/p{i}",
            query=f"q{i}",
            clicks=100,
            impressions=1000,
            position=position,
        )
        for i in range(n)
    ]


@pytest.mark.parametrize(
    ("position", "label"),
    [
        (1.0, "1"),
        (1.49, "1"),
        (2.0, "2"),
        (3.4, "3"),
        (4.0, "4-5"),
        (8.0, "6-10"),
        (15.0, "11-20"),
        (40.0, "21+"),
    ],
)
def test_position_buckets(position, label) -> None:
    assert bucket(position) == label


def test_baseline_comes_only_from_the_properties_own_data_and_needs_enough_rows() -> (
    None
):
    rows = (
        healthy_rows(30)
        + [agg(page="/x", query="few", clicks=0, impressions=500, position=15.0)] * 3
    )
    baselines = ctr_baselines(rows, min_impressions=10, min_rows=20)
    assert set(baselines) == {"3"}  # the 11-20 bucket has only 3 rows: no baseline
    assert baselines["3"].ctr == pytest.approx(0.10) and baselines["3"].rows == 30


def test_low_ctr_is_relative_to_the_own_baseline_and_ignores_unsupported_buckets() -> (
    None
):
    rows = healthy_rows(30)
    low = agg(
        page="/low", query="slow", clicks=20, impressions=2000, position=3.0
    )  # 1% vs 10%
    fine = agg(page="/fine", query="ok", clicks=90, impressions=1000, position=3.0)
    thin = agg(
        page="/thin", query="tiny", clicks=0, impressions=50, position=3.0
    )  # below min impressions
    nobase = agg(
        page="/far", query="far", clicks=0, impressions=5000, position=15.0
    )  # no 11-20 baseline
    deep = agg(page="/deep", query="deep", clicks=0, impressions=5000, position=40.0)
    found = find_low_ctr(
        rows + [low, fine, thin, nobase, deep],
        ctr_baselines(rows + [low, fine, thin]),
        T,
    )
    assert [f["page"] for f in found] == ["/low"]
    f = found[0]
    assert (
        f["baseline_ctr"] == pytest.approx(0.1, abs=0.01) and f["baseline_rows"] >= 30
    )
    assert f["expected_clicks"] > 150 and f["shortfall_clicks"] == pytest.approx(
        f["expected_clicks"] - 20, abs=0.2
    )


def test_low_ctr_needs_a_meaningful_shortfall_in_clicks() -> None:
    rows = healthy_rows(30)
    small = agg(
        page="/s", query="s", clicks=0, impressions=100, position=3.0
    )  # expected ~10, shortfall 10
    assert [f["page"] for f in find_low_ctr([small], ctr_baselines(rows), T)] == ["/s"]
    tiny_baseline = {"3": ctr_baselines(rows)["3"].__class__("3", 0.02, 30, 30000)}
    assert (
        find_low_ctr([small], tiny_baseline, T) == []
    )  # expected 2 clicks < min shortfall


# -- declines -------------------------------------------------------------------------------------


def test_clicks_decline_must_exceed_both_the_ratio_and_count_noise() -> None:
    prev = {
        "/big": agg(page="/big", clicks=200, impressions=4000, position=3.0),
        "/noise": agg(page="/noise", clicks=25, impressions=500, position=4.0),
        "/small": agg(page="/small", clicks=10, impressions=200, position=5.0),
        "/steady": agg(page="/steady", clicks=100, impressions=2000, position=3.0),
    }
    cur = {
        "/big": agg(page="/big", clicks=100, impressions=2000, position=3.1),
        "/noise": agg(
            page="/noise", clicks=17, impressions=400, position=4.0
        ),  # 32% but only 8 clicks
        "/small": agg(
            page="/small", clicks=1, impressions=20, position=5.0
        ),  # below min previous
        "/steady": agg(page="/steady", clicks=95, impressions=1900, position=3.0),
    }
    found = find_declining_clicks(cur, prev, T)
    assert [f["page"] for f in found] == ["/big"]
    assert found[0]["lost_clicks"] == 100 and found[0]["clicks_change"] == -0.5


def test_a_page_missing_from_the_current_window_counts_as_lost_not_ignored() -> None:
    prev = {"/gone": agg(page="/gone", clicks=100, impressions=2000, position=3.0)}
    found = find_declining_clicks({}, prev, T)
    assert (
        found
        and found[0]["clicks_current"] == 0
        and found[0]["driver"] == "impressions_drop"
    )


@pytest.mark.parametrize(
    ("cur", "prev", "driver"),
    [
        (
            agg(clicks=60, impressions=1400, position=6.0),
            agg(clicks=100, impressions=2000, position=3.0),
            "ranking_loss",
        ),
        (
            agg(clicks=60, impressions=1200, position=3.1),
            agg(clicks=100, impressions=2000, position=3.0),
            "impressions_drop",
        ),
        (
            agg(clicks=50, impressions=2000, position=3.0),
            agg(clicks=100, impressions=2000, position=3.0),
            "ctr_drop",
        ),
        (
            agg(clicks=70, impressions=1700, position=3.2),
            agg(clicks=100, impressions=2000, position=3.0),
            "mixed",
        ),
    ],
)
def test_decline_driver_labels_which_factor_moved(cur, prev, driver) -> None:
    label, hypotheses = decline_driver(cur, prev)
    assert (
        label == driver and hypotheses
    )  # hypotheses are listed, never asserted as the cause


def test_position_decline_needs_data_in_both_windows_and_a_worse_position() -> None:
    prev = {
        "/a": agg(page="/a", clicks=50, impressions=1000, position=3.0),
        "/b": agg(page="/b", clicks=50, impressions=1000, position=3.0),
        "/c": agg(page="/c", clicks=50, impressions=1000, position=6.0),
        "/d": agg(page="/d", clicks=5, impressions=50, position=3.0),
    }
    cur = {
        "/a": agg(page="/a", clicks=30, impressions=1000, position=6.0),  # worse by 3
        "/b": agg(
            page="/b", clicks=50, impressions=1000, position=4.0
        ),  # worse by 1: below threshold
        "/c": agg(page="/c", clicks=80, impressions=1000, position=3.0),  # improved
        "/d": agg(page="/d", clicks=1, impressions=60, position=9.0),
    }  # too few impressions before
    found = find_position_decline(cur, prev, T)
    assert [f["page"] for f in found] == ["/a"] and found[0]["position_change"] == 3.0


def test_growing_pages() -> None:
    prev = {
        "/up": agg(page="/up", clicks=100, impressions=2000, position=5.0),
        "/flat": agg(page="/flat", clicks=100, impressions=2000, position=5.0),
    }
    cur = {
        "/up": agg(page="/up", clicks=160, impressions=3000, position=4.0),
        "/flat": agg(page="/flat", clicks=105, impressions=2000, position=5.0),
        "/new": agg(page="/new", clicks=500, impressions=9000, position=2.0),
    }  # no previous: not "growing"
    assert [f["page"] for f in find_growing(cur, prev, T)] == ["/up"]


# -- new queries ------------------------------------------------------------------------------------


def test_new_queries_exclude_anything_seen_before_and_tiny_volume() -> None:
    rows = [
        agg(page="/a", query="brand new", clicks=3, impressions=40, position=8.0),
        agg(page="/b", query="brand new", clicks=1, impressions=10, position=12.0),
        agg(page="/a", query="old query", clicks=9, impressions=300, position=2.0),
        agg(page="/a", query="barely", clicks=0, impressions=4, position=30.0),
    ]
    found = find_new_queries(rows, {"old query"}, T)
    assert [f["query"] for f in found] == ["brand new"]
    assert (
        found[0]["impressions"] == 50
        and found[0]["top_page"] == "/a"
        and found[0]["pages"] == 2
    )


# -- cannibalization ----------------------------------------------------------------------------------


def test_cannibalization_requires_meaningful_shares_on_several_pages() -> None:
    rows = [
        agg(page="/a", query="split", clicks=30, impressions=600, position=4.0),
        agg(page="/b", query="split", clicks=20, impressions=400, position=6.0),
        agg(page="/a", query="dominant", clicks=90, impressions=950, position=2.0),
        agg(page="/b", query="dominant", clicks=1, impressions=50, position=15.0),
        agg(page="/a", query="small", clicks=2, impressions=40, position=5.0),
        agg(page="/b", query="small", clicks=2, impressions=40, position=5.0),
        agg(page="/a", query="deep", clicks=0, impressions=300, position=40.0),
        agg(page="/b", query="deep", clicks=0, impressions=300, position=45.0),
    ]
    found = find_cannibalization(rows, T)
    assert [f["query"] for f in found] == ["split"]
    assert [p["page"] for p in found[0]["pages"]] == ["/a", "/b"] and found[0]["pages"][
        0
    ]["share"] == 0.6


# -- new articles ------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("published", "rows", "state"),
    [
        (date(2026, 10, 28), False, "too_new"),
        (date(2026, 9, 1), False, "no_data"),
        (None, False, "no_data"),
        (date(2026, 9, 1), True, "has_data"),
        (date(2026, 11, 20), False, "not_published_yet"),
    ],
)
def test_new_article_without_data_is_explained_not_flagged(
    published, rows, state
) -> None:
    result = article_data_state(published, date(2026, 10, 31), rows)
    assert result["state"] == state
    if state != "has_data":
        assert result["note"]
    if state == "too_new":
        assert "expected, not a problem" in result["note"]


# -- recommendations ----------------------------------------------------------------------------------


def findings():
    cur = {"/big": agg(page="/big", clicks=100, impressions=2000, position=6.0)}
    prev = {"/big": agg(page="/big", clicks=200, impressions=4000, position=3.0)}
    return {
        "low_ctr": find_low_ctr(
            [agg(page="/low", query="slow", clicks=20, impressions=2000, position=3.0)],
            ctr_baselines(healthy_rows(30)),
            T,
        ),
        "declining_clicks": find_declining_clicks(cur, prev, T),
        "declining_position": find_position_decline(cur, prev, T),
        "new_query": [
            {
                "query": "fresh",
                "impressions": 80,
                "clicks": 4,
                "ctr": 0.05,
                "position": 7.0,
                "top_page": "/big",
                "pages": 1,
            }
        ],
        "cannibalization": [
            {
                "query": "split",
                "total_impressions": 1000,
                "total_clicks": 50,
                "other_pages": 0,
                "pages": [
                    {
                        "page": "/a",
                        "impressions": 600,
                        "clicks": 30,
                        "share": 0.6,
                        "position": 4.0,
                    },
                    {
                        "page": "/b",
                        "impressions": 400,
                        "clicks": 20,
                        "share": 0.4,
                        "position": 6.0,
                    },
                ],
            }
        ],
        "growing_page": [
            {
                "page": "/up",
                "clicks_current": 160,
                "clicks_previous": 100,
                "clicks_change": 0.6,
                "gained_clicks": 60,
                "impressions_current": 1,
                "impressions_previous": 1,
                "position_current": 3.0,
                "position_previous": 4.0,
            }
        ],
    }


def build(**overrides):
    args = dict(
        window=window(),
        previous_clicks=1000.0,
        current_impressions=50000.0,
        article_for_page=lambda p: "article-1" if p == "/big" else None,
        comparison_possible=True,
        history_windows=3.0,
    )
    args.update(overrides)
    return build_insights(findings(), **args)


def test_impact_is_the_share_of_the_properties_own_traffic() -> None:
    assert impact_from_share(0.06, (0.05, 0.01)) == "high"
    assert impact_from_share(0.02, (0.05, 0.01)) == "medium"
    assert impact_from_share(0.001, (0.05, 0.01)) == "low"


def test_insights_carry_evidence_ordinal_labels_and_no_scores() -> None:
    insights = build()
    kinds = {i.kind for i in insights}
    assert kinds == {
        "low_ctr",
        "declining_clicks",
        "declining_position",
        "new_query",
        "cannibalization",
        "growing_page",
    }
    assert [i.rank for i in insights] == list(range(1, len(insights) + 1))
    for i in insights:
        assert i.evidence and i.action and i.rationale and i.verification
        assert {i.impact, i.confidence, i.effort, i.risk} <= {"high", "medium", "low"}
        assert i.basis in ("measured", "inferred")
        assert "score" not in i.model_dump_json().lower()
    order = ["high_leverage", "cheap_certainty", "investigate", "maintenance", "info"]
    assert [i.tier for i in insights] == sorted(
        (i.tier for i in insights), key=order.index
    )


def test_cannibalization_is_low_confidence_inferred_and_never_a_draft_candidate() -> (
    None
):
    cannibal = next(i for i in build() if i.kind == "cannibalization")
    assert (
        cannibal.basis == "inferred"
        and cannibal.confidence == "low"
        and cannibal.refresh_candidate is False
    )
    assert cannibal.risk == "high" and "same intent" in cannibal.action
    assert "not proof" in cannibal.rationale


def test_refresh_candidates_and_article_links() -> None:
    insights = {i.kind: i for i in build()}
    assert (
        insights["low_ctr"].refresh_candidate
        and insights["declining_position"].refresh_candidate
    )
    assert insights["declining_clicks"].refresh_candidate  # ranking_loss driver
    assert (
        insights["new_query"].article_id == "article-1"
        and insights["declining_clicks"].article_id == "article-1"
    )
    assert (
        insights["growing_page"].refresh_candidate is False
        and insights["growing_page"].tier == "info"
    )


def test_comparisons_are_skipped_when_history_is_insufficient() -> None:
    kinds = {i.kind for i in build(comparison_possible=False)}
    assert kinds == {"low_ctr", "new_query", "cannibalization"}


def test_missing_days_lower_confidence() -> None:
    full = next(i for i in build() if i.kind == "declining_clicks")
    gappy = next(
        i for i in build(window=window(current_days=20)) if i.kind == "declining_clicks"
    )
    levels = ["low", "medium", "high"]
    assert levels.index(gappy.confidence) == max(0, levels.index(full.confidence) - 1)
    short = next(i for i in build(history_windows=1.0) if i.kind == "new_query")
    assert short.confidence == "low"


def test_no_data_insight_never_claims_performance() -> None:
    state = article_data_state(date(2026, 10, 28), date(2026, 10, 31), False)
    insight = no_data_insight("/new-article", state, "article-9")
    assert (
        insight.kind == "no_data_yet"
        and insight.tier == "info"
        and insight.article_id == "article-9"
    )
    assert "do not change the page" in insight.action


def test_a_row_is_judged_without_itself_in_its_own_baseline() -> None:
    healthy = healthy_rows(30)  # 10% at position 3
    heavy_low = agg(
        page="/heavy", query="heavy", clicks=30, impressions=3000, position=3.0
    )  # 1%
    rows = healthy + [heavy_low]
    baselines = ctr_baselines(rows)
    including = baselines["3"].ctr
    assert including < 0.0925  # the heavy underperformer drags the shared baseline down
    found = find_low_ctr(rows, baselines, T, leave_one_out=True)
    assert [f["page"] for f in found] == ["/heavy"]
    assert found[0]["baseline_ctr"] == pytest.approx(
        0.10, abs=0.001
    )  # the other rows only
    assert found[0]["baseline_rows"] == 30
    # with too few other rows the row cannot be judged fairly, so nothing is flagged
    thin = [
        agg(page=f"/t{i}", query=f"t{i}", clicks=100, impressions=1000, position=3.0)
        for i in range(5)
    ]
    thin.append(heavy_low)
    assert (
        find_low_ctr(thin, ctr_baselines(thin, min_rows=3), T, leave_one_out=True) == []
    )
