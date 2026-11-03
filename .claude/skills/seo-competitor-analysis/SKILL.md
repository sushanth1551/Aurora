---
name: seo-competitor-analysis
description: "SEO Intelligence competitor mode for a configured website: observes who ranks for the site's target keywords in its markets, compares competitor page structure and topic coverage with the site's own pages, finds content and keyword gaps, and stores a dated competitor snapshot with prioritised actions. Use when the user runs /seo-competitor-analysis or asks who outranks the site and what to do about it. Do NOT use for a single comparison page (seo-competitor-pages) or without any SERP source."
user-invocable: true
argument-hint: "[site-id] [keywords or topic]"
metadata:
  author: AURORA
  version: "1.0.0"
  category: seo-intelligence
---

# /seo-competitor-analysis

## When to activate

`/seo-competitor-analysis`, "who ranks above us", "competitor gap", "what are competitors doing".

## Required inputs

- Website id (default: only configured site). Keywords: the argument, else the config's
  `seed_keywords`, else stored keyword research. Markets: the config's `markets`.
- A credit budget if SerpApi or Firecrawl will be used (ask before the first paid call).

## Tools and integrations

- SERPs: AURORA SerpApi adapter (`/api/v1/seo/keywords` runs, or the adapter in a script), one
  search per keyword per market, no retries. Fallback: Claude WebSearch, recorded as
  "sampled, not localised".
- Competitor pages: `seo-data` `audit_page` (free) for structure; AURORA Firecrawl only within the
  budget. Respect robots and never bypass blocking.
- Domain-level gap: `seo-competitor-gap-analysis` only if SE Ranking is connected; otherwise mark
  it unavailable (it was blocked on 2026-11-02 for this reason).

## Execution steps

1. Load knowledge (`seo-learning-and-memory`): previous competitor snapshots and the plan for mode
   `competitor-analysis`; `start`.
2. SERP observation (`seo-toolkit-serp-analysis`): per keyword and market record the top 10
   domains and URLs, the site's position or absence, and SERP features (AI Overview, PAA, video).
3. Competitor set: domains recurring across keywords; classify with the config's `relevance`
   values. Official sources (for example exam bodies) are cited, not competed with.
4. Page comparison: for each target keyword, the site's owner page against the 2-3 best ranking
   pages: word count, H2 outline, tables/FAQ/tools, freshness, sources, schema, internal links.
5. Gaps (`seo-keyword-research`, `seo-content-strategy`): topics or sections competitors cover and
   the site does not; choose "extend existing page" before "new page" to avoid cannibalisation.
6. Comparison pages (`seo-competitor-pages`, optional) only if comparison intent shows in SERPs.
7. Prioritise (`seo-toolkit-prioritization`) and store: `research --file` with a `competitor`
   snapshot, findings for each gap (key `content-gap:<topic>`), then `finish`.

## Analysis methodology

- Positions are observations for one date, device and location; say so. Never derive traffic.
- A gap is worth acting on when several ranking pages cover it and the site's page targets the
  same intent.
- Compare with the previous snapshot: new competitors, lost or gained positions for the same
  keyword and market.

## Output format

Competitor table (domain, keywords seen, best position, relevance), per-keyword comparison,
gap list with owner page and action, changes since the previous snapshot, tools and costs.

## Quality checks

- Every position has date, market and source. No volumes or difficulty without a provider.
- No copying competitor text; summarise structure only.

## Error handling

No SERP source at all: finish as `blocked` with the reason. Budget exhausted mid-run: stop paid
calls, finish as `partial`.

## Website-specific configuration

Competitors, markets and seed keywords come from the website config.

## How findings are stored

Competitor snapshot under `sites/<site>/research/competitor/`, gaps as findings, execution and
report through `seo-learning-and-memory`.

## Improving this skill

Propose changes through `seo-learning-and-memory` (for example a competitor that proved
irrelevant twice should be reclassified in the config, which is a config change, not a skill change).
