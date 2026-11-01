# Phase 3 checklist: keyword intelligence, SERP analysis, competitors, content opportunities

**Date:** 2026-11-01. Ticked only when verified by an executed check. **Offline only:** no live
SerpApi request was made (no approval for live calls in this phase) and migration `0006` was
**not applied** to the development database.

## Delivered
- [x] Keyword discovery from seeds and site topics (stored audit); template expansions opt-in: `seo/keywords/discovery.py`
- [x] Related and long-tail keywords from observed People Also Ask and related searches
- [x] Search-intent classification with a recorded basis; unrecognised queries stay unclassified: `intent.py`
- [x] SERP extraction: organic, People Also Ask, related searches, featured snippet, AI Overview status, shopping/local/ads flags: `tools/serpapi_features.py` and `SerpApiTool.search_serp` (existing `search` unchanged; a test asserts its request parameters)
- [x] Competitor analysis and comparison (SERP competitors, position matrix): `analysis.py`
- [x] Topic clustering by shared top-10 URLs
- [x] Content gap detection with verbatim evidence; refuses to claim gaps without stored site text
- [x] Blog opportunity recommendations with ordinal impact, confidence, effort, risk and rationale
- [x] Search volume, difficulty and CPC are always `not_available` (SerpApi provides none)
- [x] Budgets (`SEO_KEYWORD_MAX_QUERIES`), approval ceiling, SERP cache (TTL), spacing, adapter retries, usage and optional cost tracking
- [x] PostgreSQL persistence: migration `0006_keyword_research` (4 additive tables), `KeywordRepository`
- [x] API: 11 token-protected routes under `/api/v1/seo/keywords`
- [x] Dashboard: Keywords tab (plan, estimate and approval, opportunities with SERP evidence, competitor comparison, gaps, selection, history)
- [x] Opportunities can be selected for article generation (recorded only; no article, no LLM)
- [x] DataForSEO: not used, not enabled

## Validation (executed)
Reproduce with `python -m pytest`, `ruff check backend scripts`, `ruff format --check backend scripts`,
`mypy backend/app` and `npm run build` in `frontend/`. Exact counts are in the Phase 3 report.

## Not done / pending approval
- Apply migrations `0003` to `0006` to the development database (with backup via `start-aurora.ps1`).
- Live verification with a small SerpApi budget (needs approval).
- PostgreSQL integration run of the new tables on a disposable database (SQLite and offline SQL checks only).
- Competitor page fetching for deeper gap analysis (titles and snippets only for now).
