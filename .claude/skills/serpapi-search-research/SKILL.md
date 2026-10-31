---
name: serpapi-search-research
description: "LOCAL REFERENCE SKILL (authored for AURORA; the serpapi/serpapi-python repository ships no Agent Skills) for Google search research through SerpApi: organic results, SERP composition, related searches and People-also-ask questions, keyword and competitor discovery, localization (gl/hl/location), pagination, json_restrictor, errors and timeouts, zero_trace/no_cache. Use when designing or reviewing search-based research, SERP analysis or keyword discovery in AURORA. AURORA calls SerpApi only through its own HTTPX adapter (SerpApiTool); never add the serpapi package or a second client."
---

# SerpApi search research (local reference)

**Status and provenance.** Written locally for AURORA on 2026-10-31. The `references/` files
are **unchanged copies** of the official `serpapi-python` documentation (MIT), and the response
and parameter names below come from SerpApi's Google Search API documentation (see *Sources*).
This is not an official SerpApi skill.

## AURORA integration boundary (read first)

- AURORA already integrates SerpApi in `backend/app/tools/serpapi_tool.py`:
  `SerpApiTool.search(query, *, num_results=10, country=None, language=None)` sends
  `engine=google`, `q`, `num` (capped), `gl` (from `country`), `hl` (from `language`) and
  `output=json` over HTTPX, retries transient failures with backoff honouring `Retry-After`,
  applies a circuit breaker, and parses **`organic_results` only** into `SearchResult`
  (`position`, `title`, `url`, `snippet`, `displayed_link`, `domain`, `metadata`).
- Do **not** install `serpapi` (the Python client documented in `references/`) or create another
  HTTP client; the client-library snippets in the references are there to explain parameters and
  response shapes, not to be copied into AURORA.
- The API key travels as a URL query parameter, so request URLs are never logged (CLAUDE.md).
- Every result URL still goes through `UrlPolicy`/`SourceManager` before any fetch.
- `related_searches`, `related_questions`, `ai_overview`, `knowledge_graph` and the other SERP
  sections are returned by SerpApi but **discarded by the current parser**. Using them is a
  Module 6 adapter change (extend `_parse`, keep provenance), not something a skill can do.
- A search result is a *snippet*, not page evidence: AURORA ranks snippet-only sources at the
  lowest credibility tier and never treats them as full-page support (`research-context-builder.md`).

## 1. Organic search for research

- Required: `q`. Localize with `gl` (country code), `hl` (interface language) and either
  `location` (city-level) **or** `uule`; `google_domain` (default `google.com`) for
  country domains such as `google.co.in`. Record the exact parameters and retrieval time with
  the results so provenance is reproducible.
- Pagination is offset based: `start=0,10,20,...`; responses carry `pagination` (Google-style
  `current`/`next`/`other_pages`) and `serpapi_pagination` (SerpApi links). AURORA's adapter
  requests a single page with `num`; more pages mean more billed searches, so bound them.
- `device` (`desktop` default, `tablet`, `mobile`) changes the SERP; keep it fixed within a study.
- `tbm` switches verticals (`nws` news, `isch` images, `shop`, `vid`, `lcl`, `bks`);
  `tbs` carries advanced filters such as date ranges; `safe`, `filter` and `lr` are documented in
  `references/parameters-and-engines.md` and the Search API page.

## 2. SERP composition analysis

Top-level sections seen in Google Search API responses (presence varies by query):
`search_information`, `organic_results`, `related_questions` (People also ask, with snippets and
source links), `related_searches`, `knowledge_graph` (`title`, `type`, `kgmid`, description),
`local_results` (`places`), `local_map`, `immersive_products`, `perspectives`,
`refine_this_search`, `refine_search_filters`, `things_to_know`, `pagination`,
`serpapi_pagination`. SerpApi also documents `answer_box`, `ai_overview`, `top_stories`,
`inline_videos` and `people_also_search_for` for other queries; confirm on the Search API page
before relying on a key. Each `organic_results` item has `position`, `title`, `link`,
`redirect_link`, `displayed_link`, `snippet`, `snippet_highlighted_words`, `sitelinks`,
`source`, and sometimes `thumbnail`/`favicon`.

Analysis rules (AURORA's `serp-analysis` and `seo-toolkit-serp-analysis` skills apply):
describe only sections that were observed; treat section presence as an intent signal, not a
ranking factor; keep retrieval date, `gl`/`hl`/`location`/`device` with every observation; a
snippet is not evidence that the page says more than the snippet.

## 3. Keyword and question discovery

- Expand a seed query with `related_searches` (follow-up queries) and `related_questions`
  (questions with answer snippets); both come free with the organic request once the parser
  exposes them. Google Trends (`engine=google_trends`, see
  `references/examples/google-trends-demand.md`) gives *relative* interest over time, not
  volumes. SerpApi organic results provide **no search volume, CPC or difficulty**; mark demand
  `not_available` rather than estimating (same rule as `seo-toolkit-keyword-research`).
- Group candidate queries by the single test "would the same page satisfy both searchers?",
  using SERP overlap between queries as the strongest available signal.

## 4. Competitor discovery

- Competitors for a query are the registrable domains recurring in `organic_results` across the
  query set (`displayed_link`/`source` help label publishers; `link` gives the destination).
- `site:` and `intitle:` operators work in `q` to inventory a competitor's indexed pages or
  titles; `tbm=nws` surfaces coverage. Respect AURORA's official-site rules: a domain is
  "official" only when the name matches **and** page text confirms it.

## 5. Request hygiene, cost and privacy

- `json_restrictor` returns only the fields you name (e.g. `organic_results.title,link,snippet`):
  smaller payloads, same billing; see `references/json-restrictor.md`.
- `no_cache=true` forces fresh results (SerpApi may otherwise serve a recent cached search);
  it cannot be combined with `async=true`, which queues the search for the Searches Archive.
- `zero_trace=true` (plan-dependent) stops SerpApi storing parameters, files and metadata;
  see `references/zero-trace.md`. `output=md` returns Markdown instead of JSON
  (`references/ai-agents.md`), which AURORA does not use.
- Errors: 4xx/5xx map to AURORA's `ProviderAuthenticationError` (401/402/403),
  `ProviderRateLimitError` (429, `Retry-After` honoured), `ProviderUnavailableError`
  (5xx, timeouts, connection failures) and `ProviderRequestError`; only retryable classes are
  retried, within `RESEARCH_MAX_RETRIES`. `references/errors-and-timeouts.md` shows the same
  classes from the client library's point of view.
- Each successful request costs one search credit; bound `num_results`, pages and query counts
  per run (`WEBSITE_RESEARCH_MAX_PROVIDER_REQUESTS`).

## References (copied unchanged from serpapi/serpapi-python `docs/`)

- `references/ai-agents.md`: agent usage and the `llms.txt` parameter index.
- `references/parameters-and-engines.md`: engines and common Google parameters.
- `references/pagination.md`, `references/output-formats.md`, `references/json-restrictor.md`,
  `references/zero-trace.md`, `references/errors-and-timeouts.md`, `references/account-and-locations.md`.
- `references/examples/`: `google-ai-overview.md`, `google-trends-demand.md`,
  `google-news-monitoring.md`, `google-maps-local-business.md`.

## Sources (retrieved 2026-10-31)

- SerpApi Google Search API: https://serpapi.com/search-api
- SerpApi parameter index for agents: https://serpapi.com/llms.txt
- serpapi-python repository and docs: https://github.com/serpapi/serpapi-python (MIT)
- AURORA adapter: `backend/app/tools/serpapi_tool.py`; evidence rules: `docs/research-context-builder.md`
