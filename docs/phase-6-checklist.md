# Phase 6 checklist: Google Search Console and continuous SEO improvement

**Date:** 2026-11-01. Ticked only when verified by an executed check. **Verified with mocks
only.** No request was made to Google, no Google account was connected, and migration `0009` was
**not applied** to the development database. The live test procedure is in
`docs/search-console.md` and needs your approval.

## Delivered
- [x] OAuth 2.0 authorization-code flow with PKCE, read-only scope, single-use expiring `state` (`monitoring/oauth.py`)
- [x] OAuth start and callback endpoints; the callback is public by necessity, rate limited, and renders a fixed page that reflects nothing
- [x] Refresh token sealed with Windows DPAPI before storage (`monitoring/vault.py`); verified round-trip on this machine; environment fallback elsewhere
- [x] Token refresh, `invalid_grant` detection and connection status with an explicit disconnected state (`not_configured`, `disconnected`, `connected`, `reauthorization_required`)
- [x] Verified-property listing and selection (unverified properties cannot be selected)
- [x] Search Analytics integration: clicks, impressions, CTR, average position by date, query and page (`tools/search_console_tool.py`)
- [x] Approval-gated sync with request ceilings, paging, idempotent inserts and honest empty results
- [x] Historical performance in PostgreSQL: migration `0009_search_console` (9 additive tables), `GscRepository`
- [x] Article-to-page-and-keyword mapping (plain UUIDs, no foreign keys); property-membership validation
- [x] Charts (SVG, stored data only, with table alternative) and tables; average-position explanation beside every position
- [x] Detectors: low CTR vs the property's own baseline (leave-one-out), declining clicks (with driver), declining position, new queries, potential cannibalization, growing pages
- [x] New articles without data are explained (`too_new`, `no_data`), never shown as zeros
- [x] Evidence-backed recommendations with impact, confidence, effort, risk, tier, verification and basis; no numeric scores
- [x] Refresh briefs for new drafts; `requires_new_approval` always true; no path to modify published content
- [x] Optional scheduler: off by default, per-schedule approved request ceiling, never started unless enabled
- [x] API: 29 routes under `/api/v1/gsc` (28 token-protected plus the public OAuth callback)
- [x] Dashboard: Search Console tab (connection, sync, performance, findings, mappings, schedules)

## Validation (executed)
See the Phase 6 report in the session for exact counts. Reproduce with `python -m pytest`,
`ruff check backend scripts`, `ruff format --check backend scripts`, `mypy backend/app` and
`npm run build` in `frontend/`.

## Not done / pending approval
- Apply migrations (`0003`–`0006`, then `0009`) to the development database, with a backup.
- Live verification against Google (needs your Google Cloud client and your approval).
- Draft generation from a refresh brief (belongs to the article workflow).
- URL Inspection, country/device dimensions, non-web search types.
- Browser-level tests of the new tab (final testing phase).

## Bugs found by the tests and fixed
- A 401 from Google did not trigger the one-time token refresh (the check read the wrong status field).
- The low-CTR baseline included the row being judged; it is now left out.
- The shared `useLoad` hook would have refetched endlessly with an inline callback.
