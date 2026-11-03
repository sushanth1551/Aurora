---
name: seo-keyword-research
description: "Organize keyword opportunities from validated data sources: primary, secondary, long-tail and semantic terms grouped by search intent, mapped to one owner page each, with cannibalisation and gap checks and dated history. Use for keyword discovery, clustering, page mapping or prioritisation when Search Console, SERP, autocomplete or provider evidence is available; used by /seo-audit and /seo-competitor-analysis. Never invents volume, difficulty or CPC."
metadata:
  author: AURORA
  version: "2.0.0"
  category: seo-intelligence
---

# seo-keyword-research

**Description:** Organize keyword opportunities from validated data sources.

## Purpose
Build a prioritized keyword set aligned to search intent and business scope, mapped to the
pages that should own each intent, and kept as dated history so later runs can compare.

## When to use it
Use when the user requests keyword discovery, clustering, or prioritization and reliable
keyword/SERP evidence is available; as the keyword step of `/seo-audit` (keyword fit) and
`/seo-competitor-analysis` (gaps). For a deep intent taxonomy, also read
`seo-toolkit-keyword-research`.

## Required inputs
- Topic, audience, locale, and business context (website config: `audiences`, `markets`,
  `seed_keywords`, `products`).
- Search terms and provider metrics, if available.
- Constraints such as product focus or excluded topics.
- Previous keyword research (`research` records of kind `keyword`) to compare against.

## Tools and integrations
- Search Console queries through AURORA (`/api/v1/gsc`): the best evidence of demand the site
  already reaches (impressions, position).
- AURORA keyword research runs (`/api/v1/seo/keywords`, SerpApi, budgeted): candidates,
  competitors and gaps; volume/difficulty/CPC are always `not_available` there.
- `seo-data` MCP `keyword_suggestions(seed, country, language)`: Google autocomplete phrases (free).
- Claude WebSearch for "People also ask" sampling, recorded as such.

## Execution workflow
1. Identify seed concepts and audience needs from the config and the site's offers.
2. Gather candidate terms from explicitly identified sources; keep the source on each term.
3. Group by intent (informational, commercial investigation, transactional, navigational) and
   topic; keep evidence and metric source attached. Mark primary (one per page), secondary,
   long-tail and semantic/entity terms.
4. Map each cluster to one owner page (existing URL, or "new page needed"). Two pages with the
   same intent is a cannibalisation finding (`cannibalization:<cluster>`), confirmed with Search
   Console page-by-query data where available.
5. Find gaps: clusters with evidence of demand and no owner page, or owner pages ranking
   outside the top 10 (`keyword-gap:<cluster>`).
6. Prioritize using available evidence and the user's goals (conversion proximity first).
7. Compare with the previous snapshot: new clusters, owner changes, positions gained or lost.
8. Mark estimates, missing metrics, and assumptions.

## Expected outputs
Keyword clusters, intent labels, owner-page map, cannibalisation and gap findings,
prioritization rationale, source metadata, follow-up research needs, and a changes-since-last
snapshot list.

## Quality requirements
Avoid keyword stuffing, respect locale and language, account for intent, and state clearly that
prioritization does not guarantee traffic or rank. One primary keyword per page.

## Error handling
Do not fabricate volume, difficulty, or CPC. Flag provider gaps and return qualitative findings
only when quantitative data is unavailable. If SERP credits are not approved, use free sources
and say which steps were reduced.

## Website-specific configuration
Seeds, markets, language, brand terms and excluded topics come from the website config; nothing
site-specific belongs in this file.

## How findings are stored
A `research` snapshot of kind `keyword` (`items`: keyword, cluster, intent, owner_page,
source, observed position if any) plus findings via `seo-learning-and-memory`.

## Improving this skill
Propose changes through `seo-learning-and-memory` (for example an intent rule that misclassified
clusters in two runs); apply only after approval with `/seo-update-skills`.
