# Phase 2 checklist — technical SEO collection, audit and recommendations

**Date:** 2026-11-01. Ticked only when verified by an executed check. **Offline only:** no live
Firecrawl or site request was made (no approval for live calls in this phase), and migration
`0005` was **not applied** to the development database.

## Delivered
- [x] HTML-derived signals from raw HTML (never Markdown): `seo/technical/html_signals.py`
- [x] Title, meta description, canonical, robots meta, headings, image alt, links, hreflang, lang, viewport, mixed content
- [x] HTTP status, allow-listed headers, every redirect hop (`UrlPolicy.trace_http`, HEAD with GET fallback); indexability signals with an explicit "not proof of indexing" note
- [x] robots.txt (RFC 9309 matcher; 4xx/5xx semantics) and XML sitemaps (defused, gzip, size caps, indexes)
- [x] JSON-LD extraction and validation (syntax / vocabulary / feature rules kept separate; retired features as info)
- [x] Broken internal links (capped HEAD checks; unchecked targets stay unverified) and duplicate titles/descriptions
- [x] 47 rule checks (each with a catalog recommendation) with affected URLs and evidence (`seo/technical/rules.py`)
- [x] Recommendations with impact, confidence, effort, risk, tier and verification (`seo/technical/recommendations.py`)
- [x] PostgreSQL persistence: migration `0005_technical_seo` (4 additive tables), `SeoAuditRepository`
- [x] API: 8 routes under `/api/v1/seo/audits` (token-protected); plan → explicit approval → run
- [x] Dashboard: Technical SEO tab (plan, estimate, approval, results, evidence, history)
- [x] Firecrawl: new `scrape_for_seo`; `scrape_url` unchanged (test asserts the Markdown request body)

## Validation (executed)
See the Phase 2 report in the session for exact counts; reproduce with:
`python -m pytest`, `ruff check backend scripts`, `ruff format --check backend scripts`,
`mypy backend/app`, `npm run build` (frontend).

## Not done / pending approval
- Apply migration `0005` (with backup via `start-aurora.ps1`), together with pending `0003`/`0004`.
- Live verification against a real site (needs approval for Firecrawl credits).
- PostgreSQL integration run of the new tables on a disposable database (SQLite + offline SQL checks only).
- Browser-level tests (final testing phase, Playwright approved for then).
