---
name: seo-performance-review
description: "SEO Intelligence performance mode for a configured website: compares Search Console clicks, impressions, CTR and position across equal periods by market, device, page and query; separates brand from non-brand demand; judges whether earlier implemented changes helped; finds low-CTR pages and query cannibalisation; stores sourced metrics. Use for /seo-performance-review or 'how is search traffic doing / did our changes work'. Do NOT use for page speed or without real performance data."
user-invocable: true
argument-hint: "[site-id] [period, e.g. last-91-days]"
metadata:
  author: AURORA
  version: "1.0.0"
  category: seo-intelligence
---

# /seo-performance-review

## When to activate

`/seo-performance-review`, "how did search do", "did the title changes work", "why did clicks drop".

## Required inputs

- Website id; period (default: the last complete 91 days against the 91 days before).
- Search Console data: AURORA's GSC integration (`/api/v1/gsc`, synced property from the config)
  or exports the user supplies. Without either, finish as `blocked`.

## Tools and integrations

- AURORA GSC endpoints (overview, top queries/pages, page detail, analysis/insights); sync only
  with the user's go-ahead. The `gsc` MCP server is optional and not installed for nextsteps.
- `seo-gsc-diagnosis` for a differential diagnosis when a drop needs explaining.

## Execution steps

1. Load knowledge: previous metrics, implemented changes with dates, the last review; `start`.
2. Performance (`seo-toolkit-performance-analysis`): final data only, equal windows, by market
   (config `markets`), device, page and query. Brand vs non-brand using `brand_name` variants.
3. Change attribution: for each implemented change older than 4 weeks, compare its pages before
   vs after deployment (not before vs after commit). Mark `verified` only with a clear,
   sufficiently large effect; otherwise "inconclusive", and say why.
4. Opportunities: positions 4-15 with low CTR, queries split across pages
   (`seo-toolkit-cannibalization`), pages losing impressions.
5. Prioritise and store: `metrics --file` (each metric with source, period, scope), findings,
   `finding ... --status verified` where justified, then `finish`.

## Analysis methodology

- State data limits: privacy-filtered query rows, page x country row hiding, short windows.
- Impressions growth with falling CTR usually means new queries, not a penalty; check positions.
- Never forecast traffic; describe the measured change and its likely cause with a confidence.

## Output format

Period table per market, device split, top movers, change-attribution table (change, deploy date,
before, after, verdict), opportunities with actions, data limitations.

## Quality checks

Every number has source and period; percentages are computed from stored values; verdicts on
changes cite the comparison windows.

## Error handling

GSC not connected: `blocked`, with the setup steps in `docs/search-console.md`. Partial data: state
coverage (for example "query rows cover 770 of 2,007 clicks").

## Website-specific configuration

Property, markets, brand name and conversion pages come from the website config.

## How findings are stored

Metrics in `metrics/performance-history.json`; findings and verdicts in the ledger; report via
`seo-learning-and-memory`.

## Improving this skill

Propose threshold changes (for example the minimum effect for "verified") only after several
reviews support them.
