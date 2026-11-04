---
name: seo-opportunities
description: "SEO Intelligence quick wins and content health for a configured website, from Search Console: striking-distance queries (positions 4-20), low CTR at good positions (measured against the site's own CTR curve), decaying pages (vs the previous and the page's best 28-day window, with the queries that lost clicks), dormant pages for prune review, merge candidates from real query overlap, and optional page value (leads per click) from a GA4/CRM conversions export. Use for /seo-opportunities, 'quick wins', 'what to improve first', 'which posts are losing traffic', 'should we merge or delete posts'."
user-invocable: true
argument-hint: "[site-id] [--conversions file.csv]"
metadata:
  author: AURORA
  version: "1.0.0"
  category: seo-intelligence
---

# /seo-opportunities

## When to activate

"Quick wins", "what should I improve first", "which pages are close to page 1", "which posts are
losing traffic", "merge or delete old posts", "which blogs bring leads", or as the opportunity step
of `/seo-audit` and `/seo-performance-review`.

## Required inputs

- Website id (default: the only configured site) with a Search Console property in its config.
- PostgreSQL running (it holds the sealed Search Console token).
- Optional: a conversions CSV (GA4 landing-page report with key events, or a CRM export) with a
  page column and a conversions/leads column. Ask the owner for it when lead value matters.

## Tools and integrations

`.venv/Scripts/python scripts/seo_insights.py opportunities <site> [--conversions file.csv]
[--execution <running id>]` (read-only Search Console API: query x page for the last two 28-day
windows, date x page for 180 days). No paid calls.

## Execution steps

1. Run the command (inside a running execution pass `--execution`).
2. Read `seo-intelligence/sites/<site>/insights/opportunities-latest.json` and the report.
3. Explain, highest value first: striking-distance pages and their queries, low-CTR snippets,
   decaying pages with their lost queries, merge candidates, dormant pages.
4. For each page the owner wants to act on, hand over to `seo-content-optimization` (edits on a
   branch after approval). Merges, redirects, noindex or deletion are owner decisions only.

## Analysis methodology

- Extra clicks = impressions x (the site's own CTR at position 3 - current CTR). The curve is
  measured in the same data; buckets under 200 impressions are left out and the value is then
  "not estimated". It is an upper-bound estimate, never a forecast.
- Low CTR: position <= 5, >= 200 impressions, CTR under half the site's CTR at that position
  (snippet/title problem, or a SERP feature or AI Overview taking the click; check the SERP).
- Decay: clicks <= 70% of the previous window and <= 60% of the page's best window, with at
  least 10 clicks before. Dormant: no impressions for 90 days.
- Merge candidate: two pages each taking >= 20% of impressions for >= 2 shared queries; the page
  with fewer clicks is the merge source.
- Lead value: extra clicks x the page's own conversions per click (periods as supplied).

## Output format

Chat summary with the top 5 actions (page, why, what to do, expected effect stated as an
estimate), then the report path. Keys: `striking-distance:<path>`, `low-ctr:<path>`,
`content-decay:<path>`, `prune-review:<path>`.

## Quality checks

Every number names its window and source; no promised rankings; never recommend deleting a page
that converts or has backlinks without checking.

## Error handling

Search Console unavailable: the run is `blocked` with the reason; do not fall back to guesses.

## Website-specific configuration

Search Console property, base URL; the latest site crawl supplies the list of known pages.

## How findings are stored

Ledger findings (keys above), `insights/opportunities-<date>.json` (+ `-latest`), the
`opportunities` execution and its report.

## Improving this skill

Through proposals (`seo-learning-and-memory`) and `/seo-update-skills`.
