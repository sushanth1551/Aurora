"""Daily reindex queue: fingerprints, statuses, batches and confirmation."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.seo.intelligence import reindex

T0 = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)


def page_html(title: str, desc: str = "d", asset: str = "a1") -> str:
    return (
        f'<html><head><title data-rh="true">{title}</title>'
        f'<meta name="description" content="{desc}"/>'
        f'<script src="/assets/index-{asset}.js"></script></head></html>'
    )


def test_fingerprint_ignores_build_assets_but_not_content() -> None:
    a, title = reindex.fingerprint(page_html("T &amp; X"), '{"content": "x"}')
    assert title == "T & X"
    assert (
        a
        == reindex.fingerprint(page_html("T &amp; X", asset="b2"), '{ "content":"x" }')[
            0
        ]
    )
    assert a != reindex.fingerprint(page_html("T2"), '{"content": "x"}')[0]
    assert (
        a
        != reindex.fingerprint(page_html("T &amp; X", desc="e"), '{"content": "x"}')[0]
    )
    assert a != reindex.fingerprint(page_html("T &amp; X"), '{"content": "y"}')[0]


def test_record_live_dates_a_change_by_its_deploy_time() -> None:
    state = reindex.ReindexState()
    assert not reindex.record_live(state, "/blog/a", "f1", "A", T0)
    assert state.pages["/blog/a"].changed_at is None  # first sight is not a change
    deployed = T0 + timedelta(hours=2)
    seen = T0 + timedelta(hours=5)
    assert reindex.record_live(state, "/blog/a", "f2", "A2", seen, deployed_at=deployed)
    assert state.pages["/blog/a"].changed_at == deployed
    assert not reindex.record_live(state, "/blog/a", "f2", "A2", seen)


def test_inspection_statuses() -> None:
    now = T0 + timedelta(days=1)
    page = reindex.PageState(path="/blog/a", changed_at=T0)
    reindex.apply_inspection(
        page, T0 - timedelta(hours=1), "Submitted and indexed", now
    )
    assert page.status == "due"  # crawled before the change
    page.requested_at = T0 + timedelta(hours=1)
    reindex.apply_inspection(
        page, T0 - timedelta(hours=1), "Submitted and indexed", now
    )
    assert page.status == "requested"
    reindex.apply_inspection(
        page,
        T0 - timedelta(hours=1),
        "Submitted and indexed",
        T0 + timedelta(days=reindex.RE_REQUEST_DAYS + 1),
    )
    assert page.status == "due"  # requested a week ago and still not crawled
    reindex.apply_inspection(
        page, T0 + timedelta(hours=3), "Submitted and indexed", now
    )
    assert page.status == "ok"
    unchanged = reindex.PageState(path="/blog/b")
    reindex.apply_inspection(unchanged, T0, "Submitted and indexed", now)
    assert unchanged.status == "ok"
    new_post = reindex.PageState(path="/blog/c")
    reindex.apply_inspection(new_post, None, "URL is unknown to Google", now)
    assert new_post.status == "due"
    reindex.apply_inspection(new_post, T0, "Crawled - currently not indexed", now)
    assert new_post.status == "due"


def due_state(n: int) -> reindex.ReindexState:
    state = reindex.ReindexState()
    for i in range(n):
        state.pages[f"/blog/p{i}"] = reindex.PageState(
            path=f"/blog/p{i}", changed_at=T0, status="due", impressions_28d=float(i)
        )
    return state


def test_batches_follow_traffic_and_the_24_hour_gap() -> None:
    state = due_state(14)
    assert reindex.is_due(state, T0)
    batch = reindex.build_batch(state, T0)
    assert batch is not None and len(batch.pages) == reindex.BATCH_SIZE
    assert batch.pages[0] == "/blog/p13"  # most impressions first
    assert batch.remaining_after == 4
    assert len(batch.newly_added) == 14  # first batch: everything is new

    lines = reindex.confirm(state, batch.id, T0)
    assert len(lines) == 10 and "requested 2026-11-03" in lines[0]
    assert state.pages["/blog/p13"].status == "requested"
    assert not reindex.is_due(state, T0 + timedelta(hours=23))
    assert reindex.is_due(state, T0 + timedelta(hours=24))
    with pytest.raises(ValueError):
        reindex.confirm(state, batch.id, T0)

    # Next day: a page changed overnight is reported as new; one was re-crawled.
    state.pages["/blog/new"] = reindex.PageState(
        path="/blog/new", changed_at=T0, status="due", impressions_28d=100
    )
    state.pages["/blog/p13"].status = "ok"
    nxt = reindex.build_batch(state, T0 + timedelta(days=1))
    assert nxt is not None
    assert nxt.pages[0] == "/blog/new"
    assert nxt.newly_added == ["/blog/new"]
    assert nxt.crawled_since_last == ["/blog/p13"]


def test_snooze_and_empty_queue() -> None:
    state = due_state(1)
    state.snooze_until = T0 + timedelta(hours=2)
    assert not reindex.is_due(state, T0)
    assert reindex.is_due(state, T0 + timedelta(hours=2))
    assert reindex.build_batch(reindex.ReindexState(), T0) is None


def test_inspect_link_and_status_markdown() -> None:
    link = reindex.inspect_link("https://x.com/", "sc-domain:x.com", "/blog/a")
    assert link.startswith("https://search.google.com/search-console/inspect?")
    assert "resource_id=sc-domain%3Ax.com" in link
    assert "id=https%3A%2F%2Fx.com%2Fblog%2Fa" in link
    md = reindex.status_markdown(due_state(2), T0)
    assert "| /blog/p0 |" in md and "needs a request: 2" in md


def test_partial_confirm_records_only_requested_pages() -> None:
    state = due_state(12)
    batch = reindex.build_batch(state, T0)
    assert batch is not None
    done, missed = batch.pages[:3], batch.pages[3:]
    with pytest.raises(ValueError):
        reindex.confirm(state, batch.id, T0, only=["/blog/not-in-batch"])
    lines = reindex.confirm(state, batch.id, T0, only=done, via="Claude in Chrome")
    assert len(lines) == 3 and "Claude in Chrome" in lines[0]
    assert all(state.pages[p].status == "requested" for p in done)
    assert all(state.pages[p].status == "due" for p in missed)  # come back next batch
    nxt = reindex.build_batch(state, T0 + timedelta(hours=24))
    assert nxt is not None and set(missed) <= set(nxt.pages)
