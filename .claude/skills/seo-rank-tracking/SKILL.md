---
name: seo-rank-tracking
description: "SEO Intelligence rank and demand tracking per market (India, US, ...): free Search Console positions per keyword and country on every run with history and movement, optional budgeted SerpApi checks of the live results (site position, top competitors, featured snippet owner, AI Overview presence and whether it cites the site, People Also Ask), and keyword demand (Search Console impressions per market, plus DataForSEO search volume, difficulty, CPC and backlink summaries when configured within a monthly USD budget). Use for /seo-rank-tracking, 'check my rankings', 'rank in India vs US', 'who owns the snippet', 'search volume', 'keywords I am missing'."
user-invocable: true
argument-hint: "[site-id] [--serp] [--max-searches N]"
metadata:
  author: AURORA
  version: "1.0.0"
  category: seo-intelligence
---

# /seo-rank-tracking

## When to activate

"Check my rankings", "rankings in India and the US", "did my keyword drop", "who has the
featured snippet", "is there an AI Overview", "search volume", "what keywords am I missing".

## Required inputs

- Website id; keywords in `rank_tracking.keywords` (else `seed_keywords`); markets in
  `rank_tracking.markets` (ISO codes, e.g. IN, US).
- Paid SERP checks: `rank_tracking.serpapi_monthly_budget` > 0 (set only with the owner's
  approval) and `SERPAPI_API_KEY`.
- Paid keyword data: `keyword_data.provider = "dataforseo"`, a `monthly_budget_usd` and
  `DATAFORSEO_LOGIN` / `DATAFORSEO_PASSWORD` in `.env` (the owner enters them).

## Tools and integrations

- `scripts/seo_insights.py ranks <site>`: Search Console query x country (free), history in
  `insights/rank-history.json`.
- `scripts/seo_insights.py ranks <site> --serp [--max-searches N] [--force]`: SerpApi, one search
  per keyword x market x device, only when due (`serp_check_every_days`) and within the month's
  budget (ledger `insights/serp-usage.json`). No retries.
- `scripts/seo_insights.py keyword-data <site> [--keywords a,b] [--backlinks]`: Search Console
  demand per market; DataForSEO when configured (ledger `insights/keyword-usage.json`, costs as
  DataForSEO reports them).

## Execution steps

1. State which parts are free and which spend credits; for paid parts, state the remaining
   budget and get the owner's go-ahead in this conversation before `--serp` or a paid
   keyword-data run.
2. Run, then read `insights/rank-tracking-latest.json` / `keyword-data-latest.json`.
3. Report per market: position, previous, movement, impressions; SERP features; demand.
4. Drops: check the live result and the ranking page before suggesting a change; gaps go to
   `seo-keyword-research` / `seo-content-strategy`.

## Analysis methodology

- Search Console position is an impression-weighted average for the window, not a single live
  rank; a live SERP check is one observation for one location and device.
- Drop finding: 3+ places worse than the previous run's position.
- Keyword gap: measured volume >= 100 and the site not in the top 20 for that market.

## Output format

Table per market; findings `keyword-ranking-drop:<kw>:<market>`,
`featured-snippet-lost:<kw>:<market>`, `keyword-gap:<kw>:<market>`, `referring-domain-gap:site`.

## Quality checks

Never invent volume, difficulty, CPC or positions; "n/m" when not measured; name budgets spent.

## Error handling

Budget 0 or used up: report a blocker, do not call the provider. Provider error: recorded, not
retried.

## Website-specific configuration

`rank_tracking`, `keyword_data`, `competitors` (direct competitors for backlink summaries).

## How findings are stored

Ledger findings, history and usage ledgers under `insights/`, the `rank-tracking` execution.

## Improving this skill

Through proposals (`seo-learning-and-memory`) and `/seo-update-skills`.
