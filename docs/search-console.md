# Google Search Console and continuous SEO improvement (Phase 6)

AURORA connects to **your own** Google Search Console with **read-only** access, stores the
performance data in PostgreSQL, and analyzes it for things worth acting on: pages that earn few
clicks for their visibility, declining pages, new queries, and possible cannibalization. It never
edits your site. A content fix becomes a brief for a **new draft**, and publishing any update needs
a new approval.

Code: `backend/app/monitoring/` (service, analysis, recommendations, OAuth, vault, scheduler),
`backend/app/tools/search_console_tool.py`, API `backend/app/api/routes/gsc.py`, migration
`0009_search_console`, UI `frontend/src/pages/SearchConsolePage.tsx`.

> **Status: verified with mocks only.** Every Google interaction in the tests is a scripted
> double. Nothing has been run against Google. See *Live test procedure* below; it needs your
> approval and your Google account.

## Disconnected states (no data is ever made up)

`GET /api/v1/gsc/status` and the dashboard show one of:

| State | Meaning | What you see |
| --- | --- | --- |
| `not_configured` | `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` missing | Setup guidance listing the missing settings; no data |
| `disconnected` | OAuth client set, no refresh token | **Connect** button |
| `connected` | A refresh token exists (stored or from the environment) | Property picker, sync, performance, analysis |
| `reauthorization_required` | Google rejected the token (`invalid_grant`: revoked or expired) | Prompt to connect again |

`status` makes no Google request. "Connected" with `verified: false` means a token exists but has
not been tested; **Test connection** makes one request and records the result.

## Setup (Google Cloud, once)

1. Open <https://console.cloud.google.com/>, create or pick a project.
2. **APIs & Services → Library**: enable **Google Search Console API**.
3. **OAuth consent screen**: user type *External*, add your Google account as a **test user**.
   Add only the scope `.../auth/webmasters.readonly`.
4. **Credentials → Create credentials → OAuth client ID**, type **Web application**. Under
   *Authorized redirect URIs* add exactly:
   `http://127.0.0.1:8000/api/v1/gsc/oauth/callback`
   (change `GOOGLE_OAUTH_REDIRECT_URI` as well if you run the API on another host or port).
5. Put the client id and secret in your **local `.env`** (AURORA never writes `.env`):
   ```
   GOOGLE_CLIENT_ID=<your client id>
   GOOGLE_CLIENT_SECRET=<your client secret>
   ```
6. Restart AURORA, open the **Search Console** tab, press **Connect Google Search Console**,
   approve read-only access in the Google tab, return, then **Load properties** and choose a
   verified property.

Notes:
- While the consent screen is in *Testing* status Google typically expires refresh tokens after
  **7 days**, so you must reconnect weekly. Publishing the app ("In production") avoids that;
  Google may show an "unverified app" warning for a personal app, which you can accept for your
  own account.
- Only properties where you are an owner, full user or restricted user can be selected;
  *unverified* ones are listed but disabled.

## How the credential is stored

- **Windows (normal case):** the refresh token is sealed with **Windows DPAPI**
  (`CryptProtectData`, current-user scope, AURORA-specific entropy) before it is written to
  PostgreSQL. Only the same Windows account on the same machine can open it, so a database dump or
  backup contains ciphertext that is useless elsewhere. AURORA holds no encryption key and needs no
  extra package.
- **Elsewhere (no DPAPI):** AURORA cannot store the token. Connect is disabled and you provide
  `GOOGLE_REFRESH_TOKEN` in `.env` instead (obtain it by running the consent flow once on a
  Windows machine, or with Google's OAuth Playground using your own client).
- `GSC_TOKEN_STORAGE` = `auto` (default), `dpapi` (require it) or `environment` (never store).
- The access token lives only in memory and is refreshed on demand. Neither token, the client
  secret, nor request bodies are logged or returned by any endpoint (tests assert this).
- **Disconnect** deletes the stored token (and, with `revoke`, asks Google to revoke it). Stored
  performance data is kept.

## OAuth security

- Authorization-code flow with **PKCE (S256)**; scope `webmasters.readonly` only. If Google
  returns a broader scope the connection is refused.
- `state` is 32 random bytes, single use, expires after `GSC_OAUTH_STATE_TTL_SECONDS` (600), and
  only its hash is kept, in memory. A restart abandons pending authorizations (just start again).
- `GET /api/v1/gsc/oauth/callback` is the one route besides health that does not need the API
  token, because Google redirects the *browser* to it. It is guarded by the single-use state and
  the public rate limit, and renders a fixed page that never reflects request data.

## Data fetched and what it means

A **fetch** is planned first (no Google call) and runs only after you approve a request ceiling.
It makes one request for per-day totals (`dimensions: [date]`, which include anonymized queries),
then pages of `date, query, page` rows (`GSC_MAX_ROWS_PER_REQUEST`, at most 25,000 each) until the
data ends or the ceiling is reached (`partially_completed`, run another fetch to continue).

- Only **final** data is stored (`dataState: final`); the newest 2–3 days are not final yet.
- Search Console keeps about 16 months; `GSC_MAX_HISTORY_DAYS` (480) bounds the start date.
- Rows are stored exactly as returned and inserted idempotently: repeating a fetch skips rows
  already stored and never overwrites them.
- Google omits **anonymized queries** from query-level rows, so query rows can sum to less than the
  daily totals. Charts use the daily totals.
- Dates are Pacific time, as in Search Console.
- Search type is `web` only. Country and device dimensions are not fetched yet.

### Average position is not exact rank

Search Console's *average position* is the average, over impressions, of the topmost position of
your site. It changes with the mix of queries, locations, devices and personalization, so a
number like 4.2 is not "rank 4 for this keyword". AURORA states this beside every position and
weights daily positions by impressions when aggregating (this can differ slightly from the
Search Console UI). Use it to see direction and relative change, not as a daily rank tracker.

## Analysis

`POST /api/v1/gsc/analysis` reads **stored** data only (no Google request). The current window is
the last `window_days` (default 28) ending at the latest stored day; the previous window is the
same length just before it. Comparisons run only when both windows have at least 80% of their
days stored; otherwise the analysis says "not enough history" and skips them.

| Finding | Rule (defaults are heuristics, adjustable per request) |
| --- | --- |
| **High impressions, low CTR** | A page+query with at least 100 impressions at position 20 or better whose CTR is below half of **this property's own** CTR at the same position (impression-weighted CTR of its other rows in the same position bucket: 1, 2, 3, 4–5, 6–10, 11–20), with at least 20 supporting rows and a shortfall of at least 3 clicks. The row being judged is excluded from its own baseline. No industry benchmark is used; with too few rows there is no baseline and nothing is flagged. |
| **Declining clicks** | A page with at least 20 clicks before that lost at least 30% **and** more than count noise (`max(10, 2·√previous)` clicks). The driver is labelled from which of impressions, CTR and position moved (`ranking_loss`, `impressions_drop`, `ctr_drop`, `mixed`) with hypotheses; the cause itself is never asserted. |
| **Declining position** | Average position worse by at least 2 with at least 100 impressions in both windows. |
| **New queries** | A query with at least 10 impressions now that never appears earlier in the stored history. "New" is relative to the stored history, and the note says so when it is shorter than two windows. |
| **Potential cannibalization** | A query with at least 100 impressions where two or more pages each have at least 20% of its impressions and an average position within 20. Sharing a query alone is not enough, and Google may show several URLs on purpose, so this is `inferred`, low confidence and recommends investigation, never an automatic change. |
| **Growing page** | The mirror of a decline (information only). |
| **No data yet** | A mapped article whose page has no stored rows. Within 14 days of publishing this is expected ("too new"); after that AURORA says it cannot tell whether the page is indexed (URL Inspection is not integrated) and suggests checking in Search Console. It is never shown as zeros. |

Each finding becomes an insight with **impact**, **confidence**, **effort** and **risk** labels
(no numeric scores), the evidence numbers, a rationale, a verification step, and its basis
(`measured` or `inferred`). Impact is the share of the property's own clicks (or impressions) at
stake: 5% / 2% or more is high, 1% / 0.5% medium. Confidence drops a step when a window has
missing days.

## Article mapping

`POST /api/v1/gsc/mappings` links a published page URL (and optionally a keyword) to an article.
The article, version and publication ids are plain UUIDs with no foreign keys, so this works
whichever way articles are stored. The page must belong to the selected property (a domain
property matches the domain and its subdomains; a URL-prefix property matches the prefix).
Mappings are deactivated, never deleted.

## Refresh briefs (drafts only)

For findings where a content change is plausible (low CTR, ranking loss or CTR drop, position
decline, new query), `POST /insights/{id}/refresh-task` creates a **brief**: the page, the evidence,
its top queries, proposed changes and the rules below. A task moves
`proposed → accepted → draft_requested → draft_ready` (or `dismissed`).

- It **never modifies** a published page and AURORA has no code path from here to a CMS.
- `requires_new_approval` is always `true`: the refreshed draft is a new article version that
  must be approved before any update is published.
- Proposals are hypotheses, not promised improvements to clicks or rankings.
- Producing the draft itself is the job of the article workflow (it consumes the brief); this
  phase only creates and tracks the brief.

## Scheduler (optional, off by default)

`GSC_SCHEDULER_ENABLED=false` by default; when false no scheduler task is started and a
direct call to `run_due` returns nothing. A schedule (`fetch` or `analyze`, at least every 6
hours) starts **disabled and unapproved**. It runs only when the scheduler is enabled, the
schedule is enabled, and its **own request ceiling was approved** (a sync schedule's `budget_requests`
must be approved exactly; changing it needs a new schedule). Each run is capped at that ceiling,
uses a 7-day lookback by default, and failures are recorded on the schedule.

## Live test procedure (not run; needs your approval)

Do this only when you decide to connect your own account. It is read-only and uses a handful of
Google requests.

1. Complete *Setup* above. Start AURORA (`start-aurora.bat`) and apply pending migrations
   (back up first; `0009` is additive).
2. Open **Search Console**. Expected: state *Disconnected*. **Connect**, approve read-only access.
   Expected: the Google tab shows "Search Console connected", the dashboard shows *Connected*.
3. **Test connection** (1 request). Expected: "Google accepted the stored authorization."
4. **Load properties** (1 request) and choose a verified property. Expected: unverified
   properties are disabled.
5. **Plan a fetch** (no request), review the estimate, approve **3** requests, fetch.
   Expected: `completed` or `partially_completed`; a few hundred rows for an active site, or
   "Google returned no rows" for a brand-new property (that is correct, not an error).
6. Check the charts against Search Console's own Performance report for the same dates
   (totals should match; AURORA's average position may differ slightly).
7. **Analyze stored data**. With under two windows of history expect the "not enough history" note.
8. Map one published page. Expected: *too new* explanation if it was published in the last
   two weeks and has no rows.
9. **Disconnect** with revoke if you do not want to keep access.

Confirm during step 5 that the request body fields (`searchType`, `dataState`, `dimensions`,
`rowLimit`, `startRow`) are accepted. They follow Google's REST reference, but the live test is the
first time they meet the real API.

## Limitations

- Not verified against Google yet (mocks only).
- Quota errors arrive as HTTP 403 and are reported as "access denied or quota exceeded" without
  automatic retry, because the shared HTTP layer maps statuses before reading the body.
- DPAPI storage is Windows-only; elsewhere use `GOOGLE_REFRESH_TOKEN`.
- No URL Inspection (indexing status), no country/device/search-appearance breakdown, no
  discover/news/image search types.
- Thresholds are heuristics. Small sites will see few findings because noise guards need volume.
- Fetches are synchronous (the request waits); there is no background worker beyond the optional
  scheduler.
- Refreshed drafts depend on the article workflow; this phase delivers the evidence-backed brief.
