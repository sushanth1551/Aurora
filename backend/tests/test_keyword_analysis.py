"""SERP feature parsing, intent, discovery, clustering, competitors, gaps, opportunities."""

import pytest

from app.seo.keywords.analysis import (
    Snapshot,
    build_opportunities,
    cluster_keywords,
    compare_competitors,
    detect_gaps,
    find_competitors,
)
from app.seo.keywords.discovery import (
    CandidatePool,
    looks_like_query,
    serp_discoveries,
    site_topic_phrases,
    template_expansions,
)
from app.seo.keywords.intent import brand_tokens_for, classify
from app.seo.keywords.models import Candidate, MetricValue, unavailable_metrics
from app.seo.keywords.text import normalize_keyword
from tests.keyword_support import EMAIL_RESULTS, organic, payload, snapshot

SITE = "www.example.com"


# -- SERP feature parsing ---------------------------------------------------------------


def test_serp_features_are_parsed_from_the_payload() -> None:
    snap = snapshot(
        "email marketing",
        EMAIL_RESULTS,
        related_questions=[
            {
                "question": "What is email marketing?",
                "snippet": "It is...",
                "link": "https://rival-one.com/what",
            },
            {"question": ""},
            "junk",
        ],
        related_searches=[
            {"query": "email marketing tools"},
            {"query": "email marketing tools"},
            {},
        ],
        answer_box={
            "type": "organic_result",
            "snippet": "Answer text",
            "link": "https://rival-two.com/a",
        },
        ai_overview={
            "text_blocks": [{"snippet": "Overview text."}],
            "references": [{"link": "https://rival-one.com/x", "title": "R1"}],
        },
        shopping_results=[{"title": "x"}],
        ads=[
            {"block_position": "top"},
            {"block_position": "bottom"},
            {"block_position": "top"},
        ],
        search_information={"total_results": 12345},
    )
    f = snap.features
    assert snap.provider_search_id == "abc123" and len(snap.organic) == 5
    assert [p.question for p in f.people_also_ask] == ["What is email marketing?"]
    assert f.people_also_ask[0].domain == "rival-one.com"
    assert f.related_searches == [
        "email marketing tools"
    ]  # deduplicated, blanks dropped
    assert f.featured_snippet and f.featured_snippet.domain == "rival-two.com"
    assert f.ai_overview.status == "present" and f.ai_overview.cited_domains == [
        "rival-one.com"
    ]
    assert f.shopping_results and (f.ads_top, f.ads_bottom) == (2, 1)
    assert f.total_results_reported == 12345


def test_missing_sections_are_reported_absent_not_invented() -> None:
    f = snapshot("q", EMAIL_RESULTS).features
    assert (
        f.people_also_ask == []
        and f.related_searches == []
        and f.featured_snippet is None
    )
    assert (
        f.ai_overview.status == "absent"
        and not f.shopping_results
        and f.total_results_reported is None
    )


@pytest.mark.parametrize(
    ("block", "status"),
    [
        (
            {"page_token": "t", "serpapi_link": "https://serpapi.com/x"},
            "requires_second_request",
        ),
        ({"text_blocks": [{"snippet": "Hello"}]}, "present"),
        ({}, "unknown"),
        ("odd", "unknown"),
    ],
)
def test_ai_overview_statuses(block, status) -> None:
    assert (
        snapshot("q", EMAIL_RESULTS, ai_overview=block).features.ai_overview.status
        == status
    )


# -- intent ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("query", "intent", "basis_prefix"),
    [
        ("how to write a welcome email", "informational", "rule:informational_term"),
        ("buy email software", "transactional", "rule:transactional_term"),
        ("email marketing pricing", "transactional", "rule:transactional_term"),
        ("best email marketing tools", "commercial", "rule:commercial_term"),
        ("mailchimp vs klaviyo", "commercial", "rule:commercial_term"),
        ("acme login", "navigational", "rule:contains_site_brand"),
        ("email deliverability", None, None),
    ],
)
def test_intent_rules(query, intent, basis_prefix) -> None:
    got, basis = classify(query, brand_tokens=brand_tokens_for("www.acme.com"))
    assert got == intent
    assert (basis or "").startswith(basis_prefix or "")
    if intent is None:
        assert basis is None  # unrecognised queries stay unclassified


def test_intent_falls_back_to_observed_serp_features_only_when_no_rule_matches() -> (
    None
):
    features = snapshot(
        "email deliverability", EMAIL_RESULTS, related_questions=[{"question": "Q?"}]
    ).features
    assert classify("email deliverability", features=features) == (
        "informational",
        "serp_feature:question_features",
    )
    shop = snapshot(
        "running shoes", EMAIL_RESULTS, shopping_results=[{"x": 1}]
    ).features
    assert classify("running shoes", features=shop)[0] == "transactional"
    assert (
        classify("best running shoes", features=shop)[0] == "commercial"
    )  # query rule wins


# -- discovery -------------------------------------------------------------------------------


def test_candidate_pool_dedupes_normalizes_and_caps() -> None:
    pool = CandidatePool(max_candidates=3)
    assert pool.add("Email  Marketing!", "seed") is pool.add(
        "email marketing", "serp_related"
    )
    assert pool.get("email marketing").source == "seed"  # first source wins
    pool.add("drip campaigns", "seed")
    pool.add("welcome series", "seed")
    assert pool.add("a fourth", "seed") is None and pool.full and len(pool.all()) == 3


def test_site_topics_skip_boilerplate_and_non_queries() -> None:
    texts = [
        "Acme | Email Marketing Software",
        "Privacy Policy",
        "Cookie settings",
        "Home",
        "Email Marketing Automation - Acme",
        "x" * 200,
        "Welcome Series Examples",
    ]
    phrases = site_topic_phrases(texts, limit=10)
    assert (
        "Email Marketing Software" in phrases and "Welcome Series Examples" in phrases
    )
    assert "Email Marketing Automation" in phrases
    assert not any("privacy" in p.lower() or "cookie" in p.lower() for p in phrases)
    assert site_topic_phrases(texts, limit=1) == ["Email Marketing Software"]
    assert not looks_like_query("Home") and looks_like_query("drip campaign examples")


def test_serp_discoveries_use_only_provider_supplied_text() -> None:
    f = snapshot(
        "q",
        EMAIL_RESULTS,
        related_questions=[{"question": "Why use drip campaigns?"}],
        related_searches=[{"query": "drip campaign software"}, {"query": "x"}],
    ).features
    found = serp_discoveries("q", f)
    assert ("Why use drip campaigns?", "serp_paa") in found
    assert ("drip campaign software", "serp_related") in found and all(
        t != "x" for t, _ in found
    )
    assert template_expansions("drip")[0] == "best drip tools"


# -- clustering ----------------------------------------------------------------------------------


def _snap(query, domains, **extra):
    results = organic(*[(d, f"{d} page", "text") for d in domains])
    return Snapshot(id=query, data=snapshot(query, results, **extra))


def test_clustering_groups_keywords_with_shared_results() -> None:
    same = ["a.com", "b.com", "c.com", "d.com"]
    snaps = {
        "email tools": _snap("email tools", same + ["e.com"]),
        "email software": _snap("email software", same + ["f.com"]),
        "pizza dough": _snap("pizza dough", ["x.com", "y.com", "z.com"]),
    }
    # organic() builds URLs as https://{domain}/p{position}: same position + domain => same URL
    clusters = cluster_keywords(snaps)
    assert clusters["email tools"] == clusters["email software"] == "email tools"
    assert clusters["pizza dough"] == "pizza dough"


def test_clustering_requires_enough_shared_urls() -> None:
    snaps = {
        "a b": _snap("a b", ["a.com", "b.com", "c.com"]),
        "c d": _snap("c d", ["a.com", "b.com", "q.com"]),
    }
    clusters = cluster_keywords(snaps)  # only 2 shared URLs
    assert clusters["a b"] != clusters["c d"]


# -- competitors ----------------------------------------------------------------------------------


def test_competitors_exclude_the_site_and_are_labelled_serp_competitors() -> None:
    snaps = {
        "email marketing": Snapshot("1", snapshot("email marketing", EMAIL_RESULTS)),
        "drip campaigns": Snapshot(
            "2",
            snapshot(
                "drip campaigns",
                organic(("rival-one.com", "Drip", "x"), ("example.com", "Acme", "y")),
            ),
        ),
    }
    competitors = find_competitors(snaps, SITE)
    assert [c.domain for c in competitors][:1] == ["rival-one.com"]
    assert "example.com" not in {c.domain for c in competitors}
    top = competitors[0]
    assert top.appearances == 2 and top.positions == {
        "drip campaigns": 1,
        "email marketing": 1,
    }
    assert top.classification == "serp_competitor"
    comparison = compare_competitors(
        competitors, ["rival-one.com", "rival-two.com", "nope.com"]
    )
    assert comparison["domains"] == ["rival-one.com", "rival-two.com"] and comparison[
        "unknown_domains"
    ] == ["nope.com"]
    assert comparison["shared_keywords"] == ["email marketing"]
    assert comparison["only_for"]["rival-one.com"] == ["drip campaigns"]


# -- content gaps ------------------------------------------------------------------------------


def test_gaps_need_site_text_and_are_never_claimed_without_it() -> None:
    snaps = {
        "email marketing": Snapshot("1", snapshot("email marketing", EMAIL_RESULTS))
    }
    gaps, status = detect_gaps(snaps, SITE, site_text=[])
    assert gaps == [] and status == "site_content_unavailable"


def test_gaps_are_phrases_shared_by_competitors_missing_from_site_text_with_verbatim_evidence() -> (
    None
):
    snaps = {
        "email marketing": Snapshot(
            "1",
            snapshot(
                "email marketing",
                EMAIL_RESULTS,
                related_questions=[
                    {
                        "question": "How do automation workflows reduce churn?",
                        "link": "https://rival-one.com/q",
                    }
                ],
            ),
        )
    }
    site_text = ["Acme Email Platform", "Welcome Series Examples", "Send campaigns"]
    gaps, status = detect_gaps(snaps, SITE, site_text)
    assert status == "ok"
    phrases = {g.term: g for g in gaps if g.kind == "phrase"}
    assert (
        "automation workflows" not in phrases
    )  # appears for one domain only (question text)
    gap = phrases["marketing automation"]
    assert (
        set(gap.competitor_domains) == {"rival-one.com", "rival-two.com"}
        and gap.basis == "observed"
    )
    assert all(
        e.excerpt and "marketing automation" in e.excerpt.lower() for e in gap.evidence
    )
    assert "welcome series" not in phrases  # the site already covers it
    assert "email marketing" not in phrases  # restates the query
    assert all(
        g.competitor_domains and "example.com" not in g.competitor_domains
        for g in gaps
        if g.kind == "phrase"
    )
    questions = [g for g in gaps if g.kind == "question"]
    assert (
        questions
        and questions[0].basis == "inferred"
        and questions[0].queries == ["email marketing"]
    )


def test_questions_the_site_already_answers_are_not_gaps() -> None:
    snaps = {
        "q": Snapshot(
            "1",
            snapshot(
                "q",
                EMAIL_RESULTS,
                related_questions=[
                    {"question": "How does email automation reduce churn?"}
                ],
            ),
        )
    }
    gaps, _ = detect_gaps(
        snaps, SITE, ["How email automation can reduce churn for subscribers"]
    )
    assert [g for g in gaps if g.kind == "question"] == []


# -- opportunities --------------------------------------------------------------------------------


def _candidates(*queries, intent=None):
    return [
        Candidate(
            keyword=q,
            normalized=normalize_keyword(q),
            source="seed",
            intent=intent,
            intent_basis="rule:test" if intent else None,
        )
        for q in queries
    ]


def test_opportunity_actions_follow_site_position_and_intent() -> None:
    results = organic(
        ("rival-one.com", "Welcome Series Ideas", "x"),
        ("rival-two.com", "Welcome Series Tips", "y"),
        ("a.com", "A", "a"),
        ("b.com", "B", "b"),
        ("example.com", "Acme", "z"),
    )
    snaps = {
        "welcome series": Snapshot(
            "s1",
            snapshot(
                "welcome series",
                results,
                related_questions=[
                    {"question": "What is a welcome series?"},
                    {"question": "Why send a welcome series?"},
                ],
            ),
        ),
        "drip campaigns": Snapshot(
            "s2",
            snapshot(
                "drip campaigns",
                organic(("example.com", "Drip", "x"), ("a.com", "A", "a")),
            ),
        ),
        "buy email software": Snapshot(
            "s3", snapshot("buy email software", EMAIL_RESULTS)
        ),
        "plain topic": Snapshot(
            "s4", snapshot("plain topic", organic(("a.com", "A", "a")))
        ),
    }
    cands = _candidates(
        "welcome series", "drip campaigns", "plain topic"
    ) + _candidates("buy email software", intent="transactional")
    ops = {o.normalized: o for o in build_opportunities(cands, snaps, [], SITE)}
    assert (
        ops["welcome series"].action == "improve_existing"
        and ops["welcome series"].serp.site_position == 5
    )
    assert (
        ops["drip campaigns"].action == "monitor"
        and ops["drip campaigns"].serp.site_position == 1
    )
    third = Snapshot(
        "s5",
        snapshot(
            "third",
            organic(
                ("a.com", "A", "a"), ("b.com", "B", "b"), ("example.com", "E", "e")
            ),
        ),
    )
    edge = build_opportunities(_candidates("third"), {"third": third}, [], SITE)[0]
    assert (
        edge.serp.site_position == 3 and edge.action == "monitor"
    )  # the top 3 are "monitor"
    assert ops["buy email software"].action == "deprioritized"
    assert (
        ops["plain topic"].action == "low_evidence"
        and ops["plain topic"].confidence == "low"
    )
    assert (
        ops["welcome series"].serp.paa_count == 2
        and ops["welcome series"].serp.snapshot_id == "s1"
    )


def test_opportunities_never_carry_estimated_metrics_and_are_ranked() -> None:
    snaps = {
        "email marketing": Snapshot(
            "1",
            snapshot(
                "email marketing",
                EMAIL_RESULTS.copy()[:2],
                related_questions=[
                    {"question": "What is email marketing?"},
                    {"question": "Why use email marketing?"},
                ],
            ),
        )
    }
    gaps, _ = detect_gaps(snaps, "other.com", ["Unrelated page about cooking"])
    ops = build_opportunities(_candidates("email marketing"), snaps, gaps, "other.com")
    assert [o.rank for o in ops] == [1]
    op = ops[0]
    assert op.action == "write_new" and op.impact in ("medium", "high")
    for name in ("search_volume", "difficulty", "cpc"):
        assert op.metrics[name] == MetricValue() and op.metrics[name].value is None
    assert any("not available" in line for line in op.rationale)
    assert op.basis == "inferred" and op.risk == "low"


def test_candidates_without_a_serp_snapshot_produce_no_opportunity() -> None:
    assert build_opportunities(_candidates("never searched"), {}, [], SITE) == []


def test_unavailable_metrics_helper() -> None:
    assert set(unavailable_metrics()) == {"search_volume", "difficulty", "cpc"}
    assert all(
        m.status == "not_available" and m.value is None
        for m in unavailable_metrics().values()
    )
    assert payload([])["organic_results"] == []


def test_a_phrase_that_is_another_searched_query_is_still_a_gap_when_the_site_lacks_it() -> (
    None
):
    """Restating the query is judged per SERP, not against every query in the run."""
    snaps = {
        "email marketing": Snapshot("1", snapshot("email marketing", EMAIL_RESULTS)),
        "email marketing automation": Snapshot(
            "2",
            snapshot(
                "email marketing automation",
                organic(
                    ("rival-one.com", "Email Marketing Automation Guide", "x"),
                    ("a.com", "A", "a"),
                ),
            ),
        ),
    }
    gaps, _ = detect_gaps(snaps, SITE, ["Acme Email Platform"])
    phrases = {g.term: g for g in gaps if g.kind == "phrase"}
    assert "marketing automation" in phrases
    assert phrases["marketing automation"].queries == [
        "email marketing"
    ]  # not the query that restates it
