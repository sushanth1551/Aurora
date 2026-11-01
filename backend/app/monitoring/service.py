"""Search Console service: connection, sync, analysis, mappings and refresh tasks.

Rules this module enforces:
- **Read-only.** Only the ``webmasters.readonly`` scope is requested; nothing here
  can change a Google property or a website.
- **No spending without approval.** A sync is planned first (no Google call) and
  runs only with an explicit approval and a request ceiling no higher than the
  plan's. Schedules carry their own individually approved ceiling.
- **No invented data.** Rows are stored as Google returned them. Analysis reports
  "insufficient history" instead of comparing windows it cannot support, and a
  page with no stored rows is explained, never given zeros.
- **No automatic content changes.** A refresh task is a brief handed to the
  article workflow; it never touches a published page and always requires a new
  approval before any update is published.
"""

import logging
from collections.abc import Callable
from datetime import date, datetime, timedelta
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from app.core.config import Settings
from app.core.exceptions import (
    ConflictException,
    ResearchException,
    ResourceNotFoundException,
)
from app.database.models import (
    GscAnalysisRunRecord,
    GscRefreshTaskRecord,
    GscScheduleRecord,
    GscSyncRunRecord,
)
from app.database.repositories.gsc_repository import GscRepository, normalize_page
from app.monitoring.analysis import (
    article_data_state,
    ctr_baselines,
    find_cannibalization,
    find_declining_clicks,
    find_growing,
    find_low_ctr,
    find_new_queries,
    find_position_decline,
)
from app.monitoring.errors import (
    GscNotConnected,
    GscOAuthError,
    GscReauthorizationRequired,
    GscStorageUnavailable,
)
from app.monitoring.models import POSITION_NOTE, Agg, InsightDraft, Thresholds, Window
from app.monitoring.oauth import SCOPE, OAuthFlows
from app.monitoring.recommendations import build_insights, no_data_insight
from app.monitoring.vault import TokenVault
from app.schemas.research import utc_now
from app.tools.errors import ProviderError
from app.tools.search_console_tool import (
    SearchConsoleTool,
    SiteEntry,
    validate_property,
)

logger = logging.getLogger(__name__)

DATA_LAG_DAYS = 3  # Search Console final data trails by about 2-3 days
TASK_TRANSITIONS: dict[str, set[str]] = {
    "proposed": {"accepted", "dismissed"},
    "accepted": {"draft_requested", "dismissed"},
    "draft_requested": {"draft_ready", "dismissed"},
    "draft_ready": {"dismissed"},
    "dismissed": set(),
}
INSIGHT_STATUSES = {"open", "accepted", "dismissed"}
MIN_SCHEDULE_HOURS = 6


class RefreshTokenSource:
    """Where the refresh token comes from: the vault-protected store, else the environment."""

    def __init__(
        self, repo: GscRepository, vault: TokenVault | None, settings: Settings
    ) -> None:
        self._repo, self._vault, self._settings = repo, vault, settings

    async def describe(self) -> str:
        """``stored``, ``environment`` or ``none`` (no secret is opened)."""
        record = await self._repo.get_connection()
        if record is not None and record.refresh_token_sealed:
            return "stored"
        return "environment" if self._settings.google_refresh_token else "none"

    async def get(self) -> str | None:
        record = await self._repo.get_connection()
        if record is not None and record.refresh_token_sealed:
            if self._vault is None:
                raise GscStorageUnavailable(
                    "A stored Search Console credential exists but this machine has no vault to "
                    "open it. Connect Search Console again."
                )
            return self._vault.open(record.refresh_token_sealed)
        token = self._settings.google_refresh_token
        return token.get_secret_value() if token else None


class GscService:
    def __init__(
        self,
        *,
        repo: GscRepository,
        settings: Settings,
        tool: SearchConsoleTool,
        tokens: RefreshTokenSource,
        vault: TokenVault | None,
        flows: OAuthFlows,
        today: Callable[[], date] = date.today,
        now: Callable[[], datetime] = utc_now,
    ) -> None:
        self._repo = repo
        self._settings = settings
        self._tool = tool
        self._tokens = tokens
        self._vault = vault
        self._flows = flows
        self._today = today
        self._now = now

    @property
    def repository(self) -> GscRepository:
        return self._repo

    # -- connection ------------------------------------------------------------------------

    def _client_configured(self) -> bool:
        return self._tool.is_configured

    async def status(self) -> dict[str, Any]:
        """Connection state without any Google call."""
        record = await self._repo.get_connection()
        source = await self._tokens.describe()
        if not self._client_configured():
            state = "not_configured"
        elif source == "none":
            state = "disconnected"
        elif record is not None and record.state == "reauthorization_required":
            state = "reauthorization_required"
        else:
            state = "connected"
        return {
            "state": state,
            "oauth_client_configured": self._client_configured(),
            "missing_settings": self._tool.missing_settings,
            "token_source": source,
            "can_store_token": self._vault is not None,
            "token_storage": self._vault.name if self._vault else "environment_only",
            "redirect_uri": self._settings.google_oauth_redirect_uri,
            "scope": SCOPE,
            "selected_property": record.selected_property if record else None,
            "connected_at": record.connected_at if record else None,
            "last_verified_at": record.last_verified_at if record else None,
            "last_error_category": record.last_error_category if record else None,
            "scheduler_enabled": self._settings.gsc_scheduler_enabled,
            "verified": bool(record and record.last_verified_at),
            "position_note": POSITION_NOTE,
        }

    async def begin_authorization(self) -> dict[str, Any]:
        self._tool.ensure_configured()
        if self._vault is None:
            raise GscStorageUnavailable(
                "This machine has no secure place to store a refresh token (Windows DPAPI is "
                "unavailable). Put GOOGLE_REFRESH_TOKEN in .env instead; see docs/search-console.md."
            )
        start = self._flows.begin(self._tool.client_id, self._tool.redirect_uri)
        return {
            "authorization_url": start.url,
            "expires_in_seconds": start.expires_in_seconds,
        }

    async def complete_authorization(
        self, *, code: str | None, state: str | None, error: str | None
    ) -> None:
        """Handle Google's redirect: validate ``state``, exchange the code, seal the token."""
        if error:
            raise GscOAuthError(
                "Authorization was cancelled or refused in Google's consent screen."
            )
        if not code or not state:
            raise GscOAuthError("The authorization response is incomplete.")
        verifier = self._flows.consume(state)
        if verifier is None:
            raise GscOAuthError(
                "The authorization link is invalid, expired or was already used. Start again."
            )
        if self._vault is None:
            raise GscStorageUnavailable(
                "No secure token storage is available on this machine."
            )
        grant = await self._tool.exchange_code(code, verifier)
        sealed = self._vault.seal(grant.refresh_token)
        await self._repo.save_connection(
            state="connected", sealed=sealed, vault=self._vault.name, scope=grant.scope
        )
        self._tool.forget_access_token()

    async def verify_connection(self) -> dict[str, Any]:
        """Refresh the access token (one Google call) and record the outcome."""
        try:
            await self._tool.verify_connection()
        except GscReauthorizationRequired:
            await self._repo.set_connection_state(
                "reauthorization_required", error="invalid_grant"
            )
            raise
        except ProviderError as exc:
            record = await self._repo.get_connection()
            await self._repo.set_connection_state(
                record.state if record else "connected", error=exc.code
            )
            raise
        await self._repo.set_connection_state("connected", verified=True)
        return await self.status()

    async def list_properties(self) -> list[SiteEntry]:
        return await self._tool.list_sites()

    async def select_property(self, site_url: str) -> dict[str, Any]:
        validate_property(site_url)
        sites = await self._tool.list_sites()
        match = next((s for s in sites if s.site_url == site_url), None)
        if match is None or not match.verified:
            raise ResearchException(
                "That property is not available to the authorized account as a verified property."
            )
        await self._repo.set_property(site_url)
        return await self.status()

    async def disconnect(self, *, revoke: bool) -> dict[str, Any]:
        """Remove the stored credential; optionally revoke it at Google (best effort)."""
        if revoke:
            try:
                token = await self._tokens.get()
                if token:
                    await self._tool.revoke(token)
            except Exception as exc:  # noqa: BLE001 - revocation is best effort
                logger.warning("gsc_revoke_failed error=%s", type(exc).__name__)
        await self._repo.save_connection(
            state="disconnected", sealed=None, vault=None, scope=None
        )
        self._tool.forget_access_token()
        return await self.status()

    async def property_for(self, requested: str | None) -> str:
        return await self._property(requested)

    async def _property(self, requested: str | None) -> str:
        record = await self._repo.get_connection()
        chosen = requested or (record.selected_property if record else None)
        if not chosen:
            raise ResearchException("Select a Search Console property first.")
        return validate_property(chosen)

    # -- sync -------------------------------------------------------------------------------------

    def _default_range(self) -> tuple[date, date]:
        end = self._today() - timedelta(days=DATA_LAG_DAYS)
        return end - timedelta(days=27), end

    async def plan_sync(
        self,
        *,
        property_id: str | None = None,
        start: date | None = None,
        end: date | None = None,
        max_requests: int | None = None,
        source: str = "manual",
        schedule_id: UUID | None = None,
    ) -> GscSyncRunRecord:
        prop = await self._property(property_id)
        default_start, default_end = self._default_range()
        start, end = start or default_start, end or default_end
        today = self._today()
        if start > end:
            raise ResearchException("The start date is after the end date.")
        if end > today:
            raise ResearchException("The end date is in the future.")
        if start < today - timedelta(days=self._settings.gsc_max_history_days):
            raise ResearchException(
                f"Search Console keeps about {self._settings.gsc_max_history_days} days of data; "
                "the start date is older than that."
            )
        cap = self._settings.gsc_max_requests_per_sync
        if max_requests is not None and max_requests > cap:
            raise ResearchException(
                f"max_requests may not exceed the configured maximum of {cap}."
            )
        requests = max_requests or cap
        rows = self._settings.gsc_max_rows_per_request
        record = GscSyncRunRecord(
            id=uuid4(),
            property=prop,
            start_date=start,
            end_date=end,
            status="planned",
            source=source,
            schedule_id=schedule_id,
            limits={"max_requests": requests, "rows_per_request": rows},
            estimate={
                "requests_max": requests,
                "rows_max": (requests - 1) * rows,
                "days": (end - start).days + 1,
                "notes": [
                    "Search Console API reads are free but quota-limited: one request for per-day "
                    "totals, then pages of up to the row limit for query/page rows.",
                    "Only final data is stored; the most recent 2-3 days are not final yet.",
                    "Rows Google anonymizes are omitted from query-level data.",
                ],
            },
            approval=None,
            usage={},
            created_at=self._now(),
        )
        return await self._repo.create_sync(record)

    async def get_sync(
        self, sync_id: UUID, *, refresh: bool = False
    ) -> GscSyncRunRecord:
        record = await self._repo.get_sync(sync_id)
        if record is None:
            raise ResourceNotFoundException("Sync not found.")
        if refresh:
            await self._repo.refresh(record)
        return record

    async def run_sync(
        self, sync_id: UUID, *, max_requests: int, approved_by: str = "user"
    ) -> GscSyncRunRecord:
        sync = await self.get_sync(sync_id)
        if sync.status != "planned":
            raise ConflictException(
                f"Sync is '{sync.status}'; only a planned sync can be run."
            )
        planned = int(sync.limits["max_requests"])
        if max_requests > planned:
            raise ResearchException(
                f"max_requests ({max_requests}) exceeds the plan's maximum ({planned}); plan a new sync."
            )
        if max_requests < 2:
            raise ResearchException(
                "A sync needs at least 2 requests (daily totals plus one page of rows)."
            )
        self._tool.ensure_configured()
        if await self._tokens.describe() == "none":
            raise GscNotConnected()
        if await self._repo.running_sync(sync.property) is not None:
            raise ConflictException("Another sync of this property is already running.")
        approval = {
            "approved_at": self._now().isoformat(),
            "approved_by": approved_by,
            "max_requests": max_requests,
        }
        if not await self._repo.mark_sync_running(sync_id, approval):
            raise ConflictException("The sync was started by another request.")

        usage: dict[str, Any] = {
            "requests": 0,
            "rows_fetched": 0,
            "rows_inserted": 0,
            "rows_skipped": 0,
            "daily_inserted": 0,
            "stopped_reason": None,
        }
        status, error = "completed", None
        try:
            await self._fetch(sync, max_requests, usage)
            if usage["stopped_reason"]:
                status = "partially_completed"
        except GscReauthorizationRequired as exc:
            await self._repo.set_connection_state(
                "reauthorization_required", error="invalid_grant"
            )
            status, error = "failed", exc.message
        except ProviderError as exc:
            status = (
                "partially_completed"
                if usage["rows_inserted"] or usage["daily_inserted"]
                else "failed"
            )
            error = f"{exc.code}: {exc.message}"
        except GscNotConnected as exc:
            status, error = "failed", exc.message
        except Exception as exc:  # noqa: BLE001 - recorded, never leaked
            logger.error(
                "gsc_sync_failed sync_id=%s error=%s", sync_id, type(exc).__name__
            )
            status, error = "failed", "The sync failed unexpectedly."
        await self._repo.finish_sync(sync_id, status=status, usage=usage, error=error)
        return await self.get_sync(sync_id, refresh=True)

    async def _fetch(
        self, sync: GscSyncRunRecord, max_requests: int, usage: dict[str, Any]
    ) -> None:
        start, end = sync.start_date.isoformat(), sync.end_date.isoformat()
        rows_per_request = int(sync.limits["rows_per_request"])

        daily = await self._tool.query_performance(
            sync.property, start, end, dimensions=("date",), row_limit=rows_per_request
        )
        usage["requests"] += 1
        usage["rows_fetched"] += len(daily)
        inserted, _ = await self._repo.insert_daily(
            sync.property,
            [
                (
                    date.fromisoformat(r.keys[0]),
                    r.clicks,
                    r.impressions,
                    r.ctr,
                    r.position,
                )
                for r in daily
            ],
            sync.id,
        )
        usage["daily_inserted"] = inserted

        offset = 0
        while True:
            if usage["requests"] >= max_requests:
                usage["stopped_reason"] = "request_budget"
                return
            page = await self._tool.query_performance(
                sync.property,
                start,
                end,
                dimensions=("date", "query", "page"),
                row_limit=rows_per_request,
                start_row=offset,
            )
            usage["requests"] += 1
            usage["rows_fetched"] += len(page)
            fresh, skipped = await self._repo.insert_performance(
                sync.property,
                [
                    {
                        "date": date.fromisoformat(r.keys[0]),
                        "query": r.keys[1],
                        "page": r.keys[2],
                        "search_type": "web",
                        "clicks": r.clicks,
                        "impressions": r.impressions,
                        "ctr": r.ctr,
                        "position": r.position,
                    }
                    for r in page
                ],
                sync.id,
            )
            usage["rows_inserted"] += fresh
            usage["rows_skipped"] += skipped
            if len(page) < rows_per_request:
                return
            offset += len(page)

    # -- reading stored data ---------------------------------------------------------------------

    async def overview(
        self, property_id: str | None, start: date | None, end: date | None
    ) -> dict[str, Any]:
        prop = await self._property(property_id)
        first, last = await self._repo.date_range(prop)
        if last is None:
            return {
                "property": prop,
                "has_data": False,
                "series": [],
                "totals": None,
                "note": "No Search Console data is stored for this property yet. Run a sync.",
                "position_note": POSITION_NOTE,
            }
        end = min(end or last, last)
        start = max(start or end - timedelta(days=27), first or end)
        series = await self._repo.daily_series(prop, start, end)
        clicks = sum(s.clicks for s in series)
        impressions = sum(s.impressions for s in series)
        weighted = sum(s.position * s.impressions for s in series)
        return {
            "property": prop,
            "has_data": bool(series),
            "first_date": first,
            "last_date": last,
            "start": start,
            "end": end,
            "series": [
                {
                    "date": s.date,
                    "clicks": s.clicks,
                    "impressions": s.impressions,
                    "ctr": s.ctr,
                    "position": s.position,
                }
                for s in series
            ],
            "totals": {
                "clicks": clicks,
                "impressions": impressions,
                "ctr": clicks / impressions if impressions else 0.0,
                "position": weighted / impressions if impressions else None,
            },
            "days_with_data": len(series),
            "days_in_range": (end - start).days + 1,
            "note": "Stored final data; the most recent 2-3 days are not available yet.",
            "position_note": POSITION_NOTE,
        }

    async def top(
        self,
        property_id: str | None,
        by: str,
        start: date | None,
        end: date | None,
        limit: int,
    ) -> dict[str, Any]:
        prop = await self._property(property_id)
        _, last = await self._repo.date_range(prop)
        if last is None:
            return {"property": prop, "has_data": False, "items": []}
        end = min(end or last, last)
        start = start or end - timedelta(days=27)
        rows = await self._repo.aggregate(prop, start, end, by=by)
        rows.sort(
            key=lambda a: (-a.clicks, -a.impressions, a.page or "", a.query or "")
        )
        return {
            "property": prop,
            "has_data": True,
            "start": start,
            "end": end,
            "items": [
                {
                    "page": a.page,
                    "query": a.query,
                    "clicks": a.clicks,
                    "impressions": a.impressions,
                    "ctr": a.ctr,
                    "position": a.position,
                }
                for a in rows[:limit]
            ],
            "position_note": POSITION_NOTE,
        }

    async def page_detail(
        self, property_id: str | None, page: str, start: date | None, end: date | None
    ) -> dict[str, Any]:
        prop = await self._property(property_id)
        _, last = await self._repo.date_range(prop)
        mapping = [
            m
            for m in await self._repo.mappings(prop)
            if normalize_page(m.page_url) == normalize_page(page)
        ]
        published = next((m.published_at for m in mapping if m.published_at), None)
        if last is None or not await self._repo.page_has_rows(prop, page):
            state = article_data_state(
                published.date() if published else None, self._today(), False
            )
            return {
                "property": prop,
                "page": page,
                "has_data": False,
                "state": state,
                "series": [],
                "queries": [],
                "position_note": POSITION_NOTE,
            }
        end = min(end or last, last)
        start = start or end - timedelta(days=27)
        series = await self._repo.page_series(prop, page, start, end)
        queries = await self._repo.page_queries(prop, page, start, end, limit=25)
        return {
            "property": prop,
            "page": page,
            "has_data": True,
            "start": start,
            "end": end,
            "state": article_data_state(
                published.date() if published else None, self._today(), True
            ),
            "series": [
                {
                    "date": s.query,
                    "clicks": s.clicks,
                    "impressions": s.impressions,
                    "ctr": s.ctr,
                    "position": s.position,
                }
                for s in series
            ],
            "queries": [
                {
                    "query": q.query,
                    "clicks": q.clicks,
                    "impressions": q.impressions,
                    "ctr": q.ctr,
                    "position": q.position,
                }
                for q in queries
            ],
            "position_note": POSITION_NOTE,
        }

    # -- mappings ------------------------------------------------------------------------------------

    def _page_belongs(self, property_id: str, page_url: str) -> bool:
        parts = urlsplit(page_url)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            return False
        if property_id.startswith("sc-domain:"):
            domain = property_id.removeprefix("sc-domain:").lower()
            host = parts.hostname.lower()
            return host == domain or host.endswith("." + domain)
        return page_url.startswith(property_id)

    async def add_mapping(
        self,
        *,
        property_id: str | None,
        page_url: str,
        keyword: str | None,
        article_id: UUID | None,
        article_version_id: UUID | None,
        publication_id: UUID | None,
        published_at: datetime | None,
        source: str = "manual",
    ) -> Any:
        prop = await self._property(property_id)
        if not self._page_belongs(prop, page_url):
            raise ResearchException(
                "That page URL does not belong to the selected property."
            )
        return await self._repo.upsert_mapping(
            property_id=prop,
            page_url=page_url,
            keyword=(keyword or "").strip() or None,
            article_id=article_id,
            article_version_id=article_version_id,
            publication_id=publication_id,
            source=source,
            published_at=published_at,
        )

    # -- analysis --------------------------------------------------------------------------------------

    async def analyze(
        self, *, property_id: str | None, thresholds: Thresholds, source: str = "manual"
    ) -> GscAnalysisRunRecord:
        prop = await self._property(property_id)
        notes: list[str] = []
        mappings = await self._repo.mappings(prop)
        article_by_page = {
            normalize_page(m.page_url): str(m.article_id)
            for m in mappings
            if m.article_id
        }

        def article_for(page: str) -> str | None:
            return article_by_page.get(normalize_page(page))

        first, last = await self._repo.date_range(prop)
        drafts: list[InsightDraft] = []
        window: dict[str, Any] = {}
        summary: dict[str, Any] = {}
        status = "completed"

        if last is None:
            status = "no_data"
            notes.append(
                "No Search Console data is stored for this property. Run a sync first."
            )
        else:
            days = thresholds.window_days
            cur_end = last
            cur_start = cur_end - timedelta(days=days - 1)
            prev_end = cur_start - timedelta(days=1)
            prev_start = prev_end - timedelta(days=days - 1)
            cur_days = await self._repo.days_with_data(prop, cur_start, cur_end)
            prev_days = await self._repo.days_with_data(prop, prev_start, prev_end)
            win = Window(
                current_start=cur_start,
                current_end=cur_end,
                previous_start=prev_start,
                previous_end=prev_end,
                days=days,
                current_days_with_data=cur_days,
                previous_days_with_data=prev_days,
                history_start=first,
            )
            window = win.model_dump(mode="json")
            comparison = (
                win.coverage("current") >= thresholds.min_window_coverage
                and win.coverage("previous") >= thresholds.min_window_coverage
            )
            if not comparison:
                notes.append(
                    f"Not enough history to compare periods: the current window has {cur_days} of "
                    f"{days} days and the previous one {prev_days} of {days}. Decline, position and "
                    "growth checks were skipped; sync more history to enable them."
                )
            history_days = ((last - first).days + 1) if first else 0
            history_windows = history_days / days
            if history_windows < 2:
                notes.append(
                    "Stored history is shorter than two windows, so 'new' queries are only "
                    "new relative to the stored history."
                )
            notes.append(
                "Search Console final data trails by 2-3 days, so the newest days are missing."
            )

            pages_cur = {
                a.page: a
                for a in await self._repo.aggregate(prop, cur_start, cur_end, by="page")
                if a.page
            }
            pages_prev: dict[str, Agg] = {}
            if comparison:
                pages_prev = {
                    a.page: a
                    for a in await self._repo.aggregate(
                        prop, prev_start, prev_end, by="page"
                    )
                    if a.page
                }
            pq_cur = await self._repo.aggregate(
                prop, cur_start, cur_end, by="page_query", min_impressions=10
            )
            baselines = ctr_baselines(
                pq_cur, min_impressions=10, min_rows=thresholds.min_baseline_rows
            )
            if not baselines:
                notes.append(
                    "Too few page/query rows to build the property's own CTR baseline; the "
                    "low-CTR check was skipped."
                )
            seen = (
                await self._repo.seen_queries_before(prop, cur_start)
                if first and first < cur_start
                else set()
            )

            findings: dict[str, list[dict[str, Any]]] = {
                "low_ctr": find_low_ctr(
                    pq_cur, baselines, thresholds, leave_one_out=True
                ),
                "cannibalization": find_cannibalization(pq_cur, thresholds),
                "new_query": find_new_queries(pq_cur, seen, thresholds)
                if first and first < cur_start
                else [],
            }
            if comparison:
                findings["declining_clicks"] = find_declining_clicks(
                    pages_cur, pages_prev, thresholds
                )
                findings["declining_position"] = find_position_decline(
                    pages_cur, pages_prev, thresholds
                )
                findings["growing_page"] = find_growing(
                    pages_cur, pages_prev, thresholds
                )
            if first and not first < cur_start:
                notes.append(
                    "No data exists before the current window, so new-query detection was skipped."
                )
            drafts = build_insights(
                findings,
                window=win,
                previous_clicks=sum(a.clicks for a in pages_prev.values()),
                current_impressions=sum(a.impressions for a in pages_cur.values()),
                article_for_page=article_for,
                comparison_possible=comparison,
                history_windows=history_windows,
            )
            summary["current"] = {
                "clicks": sum(a.clicks for a in pages_cur.values()),
                "impressions": sum(a.impressions for a in pages_cur.values()),
            }
            summary["comparison_possible"] = comparison

        for m in mappings:
            if await self._repo.page_has_rows(prop, m.page_url):
                continue
            state = article_data_state(
                m.published_at.date() if m.published_at else None, self._today(), False
            )
            drafts.append(
                no_data_insight(
                    m.page_url, state, str(m.article_id) if m.article_id else None
                )
            )
        for position, draft in enumerate(drafts, start=1):
            draft.rank = position
        counts: dict[str, int] = {}
        for draft in drafts:
            counts[draft.kind] = counts.get(draft.kind, 0) + 1
        summary["insights"] = len(drafts)
        summary["by_kind"] = counts
        return await self._repo.save_analysis(
            property_id=prop,
            status=status,
            params=thresholds.model_dump(),
            window=window,
            summary=summary,
            notes=notes,
            insights=drafts,
            source=source,
        )

    async def set_insight_status(self, insight_id: UUID, status: str) -> Any:
        if status not in INSIGHT_STATUSES:
            raise ResearchException("status must be open, accepted or dismissed.")
        record = await self._repo.set_insight_status(insight_id, status)
        if record is None:
            raise ResourceNotFoundException("Insight not found.")
        return record

    # -- refresh tasks ------------------------------------------------------------------------------------

    async def create_refresh_task(self, insight_id: UUID) -> GscRefreshTaskRecord:
        """Turn an insight into a brief for a refreshed *draft*. Never edits content."""
        insight = await self._repo.get_insight(insight_id)
        if insight is None:
            raise ResourceNotFoundException("Insight not found.")
        if not insight.refresh_candidate:
            raise ConflictException(
                "This finding does not call for a content refresh (it needs investigation or no action)."
            )
        existing = await self._repo.task_for_insight(insight_id)
        if existing is not None:
            return existing
        run = await self._repo.get_analysis(insight.analysis_id)
        if run is None:
            raise ResourceNotFoundException("Analysis not found.")
        ev = insight.evidence
        page = (
            insight.subject
            if insight.subject_type == "page"
            else (ev.get("top_page") or ev.get("page"))
        )
        if not page:
            raise ConflictException("The finding names no page to refresh.")
        window = run.window
        queries: list[dict[str, Any]] = []
        if window:
            cur_start, cur_end = (
                date.fromisoformat(window["current_start"]),
                date.fromisoformat(window["current_end"]),
            )
            queries = [
                {
                    "query": q.query,
                    "clicks": q.clicks,
                    "impressions": q.impressions,
                    "ctr": round(q.ctr, 4),
                    "position": round(q.position, 2) if q.position else None,
                }
                for q in await self._repo.page_queries(
                    run.property, page, cur_start, cur_end, limit=10
                )
            ]
        proposals = {
            "low_ctr": [
                f"Draft alternative titles and meta descriptions that reflect the query '{ev.get('query')}'.",
                "Keep what already ranks: change the snippet, not the page's topic.",
            ],
            "declining_clicks": [
                "Compare the page with the current top results for its main queries.",
                "Update outdated sections; keep sections that still match the top queries.",
            ],
            "declining_position": [
                "Compare the page with the current top results for its main queries.",
                "Strengthen the sections that match the queries that lost position.",
            ],
            "new_query": [
                f"Add a section that answers '{ev.get('query')}' directly.",
                "If the intent differs from the page's topic, plan a separate article instead.",
            ],
        }.get(insight.kind, ["Review the evidence and propose changes."])
        brief = {
            "kind": insight.kind,
            "reason": insight.rationale,
            "evidence": ev,
            "page_top_queries": queries,
            "proposals": proposals,
            "verification": insight.verification,
            "constraints": [
                "Do not modify the published page.",
                "Produce a new draft version of the article.",
                "A new approval is required before any update is published.",
                "Proposals are hypotheses; they are not guaranteed to improve clicks or rankings.",
            ],
            "position_note": POSITION_NOTE,
        }
        now = self._now()
        task = GscRefreshTaskRecord(
            id=uuid4(),
            insight_id=insight.id,
            property=run.property,
            page_url=page,
            article_id=insight.article_id,
            article_version_id=None,
            brief=brief,
            status="proposed",
            requires_new_approval=True,
            note=None,
            created_at=now,
            updated_at=now,
        )
        created = await self._repo.create_task(task)
        await self._repo.set_insight_status(insight.id, "accepted")
        return created

    async def update_refresh_task(
        self, task_id: UUID, *, status: str, note: str | None
    ) -> GscRefreshTaskRecord:
        task = await self._repo.get_task(task_id)
        if task is None:
            raise ResourceNotFoundException("Refresh task not found.")
        if status != task.status and status not in TASK_TRANSITIONS.get(
            task.status, set()
        ):
            raise ConflictException(
                f"A task in '{task.status}' cannot move to '{status}'."
            )
        updated = await self._repo.update_task(task_id, status=status, note=note)
        if updated is None:  # deleted between the read and the update
            raise ResourceNotFoundException("Refresh task not found.")
        return updated

    # -- schedules -----------------------------------------------------------------------------------------

    async def create_schedule(
        self,
        *,
        name: str,
        property_id: str | None,
        kind: str,
        interval_hours: int,
        lookback_days: int,
        budget_requests: int,
    ) -> GscScheduleRecord:
        prop = await self._property(property_id)
        if kind not in ("sync", "analyze"):
            raise ResearchException("kind must be 'sync' or 'analyze'.")
        if interval_hours < MIN_SCHEDULE_HOURS:
            raise ResearchException(
                f"interval_hours must be at least {MIN_SCHEDULE_HOURS}."
            )
        cap = self._settings.gsc_max_requests_per_sync
        if budget_requests > cap or (kind == "sync" and budget_requests < 2):
            raise ResearchException(
                f"budget_requests must be between 2 and {cap} for a sync schedule."
            )
        record = GscScheduleRecord(
            id=uuid4(),
            name=name.strip()[:120],
            property=prop,
            kind=kind,
            interval_hours=interval_hours,
            lookback_days=lookback_days,
            budget_requests=0 if kind == "analyze" else budget_requests,
            approved_by=None,
            approved_at=None,
            enabled=False,
            next_run_at=None,
            last_run_at=None,
            last_status=None,
            last_error=None,
            created_at=self._now(),
        )
        return await self._repo.create_schedule(record)

    async def approve_schedule(
        self, schedule_id: UUID, *, approved_by: str, budget_requests: int
    ) -> GscScheduleRecord:
        record = await self._schedule(schedule_id)
        if budget_requests != record.budget_requests:
            raise ResearchException(
                "The approved ceiling must equal the schedule's budget_requests "
                f"({record.budget_requests}); change the schedule first."
            )
        record.approved_by = approved_by.strip()[:120] or "user"
        record.approved_at = self._now()
        await self._repo.save(record)
        return record

    async def set_schedule_enabled(
        self, schedule_id: UUID, enabled: bool
    ) -> GscScheduleRecord:
        record = await self._schedule(schedule_id)
        if enabled and record.approved_at is None:
            raise ConflictException(
                "Approve the schedule's budget ceiling before enabling it."
            )
        record.enabled = enabled
        record.next_run_at = (
            self._now() + timedelta(hours=record.interval_hours) if enabled else None
        )
        await self._repo.save(record)
        return record

    async def _schedule(self, schedule_id: UUID) -> GscScheduleRecord:
        record = await self._repo.get_schedule(schedule_id)
        if record is None:
            raise ResourceNotFoundException("Schedule not found.")
        return record

    async def run_schedule(self, record: GscScheduleRecord) -> str:
        """Execute one due schedule within its own approved ceiling; returns the status."""
        if record.kind == "analyze":
            await self.analyze(
                property_id=record.property, thresholds=Thresholds(), source="schedule"
            )
            return "completed"
        end = self._today() - timedelta(days=DATA_LAG_DAYS)
        start = end - timedelta(days=max(1, record.lookback_days) - 1)
        plan = await self.plan_sync(
            property_id=record.property,
            start=start,
            end=end,
            max_requests=record.budget_requests,
            source="schedule",
            schedule_id=record.id,
        )
        done = await self.run_sync(
            plan.id,
            max_requests=record.budget_requests,
            approved_by=record.approved_by or "schedule",
        )
        return done.status
