---
name: seo-competitor-watch
description: "SEO Intelligence competitor change watch for a configured website: reads each direct/content competitor's robots.txt and XML sitemaps (free, bounded, through UrlPolicy), compares with the previous snapshot and reports new, removed and refreshed URLs, flags new competitor pages on the site's keywords and topics the site does not cover, and fetches a few titles. Use for /seo-competitor-watch, 'what did competitors publish', 'new competitor pages', 'are competitors updating content'."
user-invocable: true
argument-hint: "[site-id]"
metadata:
  author: AURORA
  version: "1.0.0"
  category: seo-intelligence
---

# /seo-competitor-watch

## When to activate

"What are competitors publishing", "new competitor content", "did they update their pages", or
as the change step of `/seo-competitor-analysis`.

## Required inputs

Website id; `competitors` with `relevance` direct or content; `seed_keywords` /
`rank_tracking.keywords`.

## Tools and integrations

`scripts/seo_insights.py competitors <site> [--execution <id>]`. Sitemap URL lists are kept in
`data/seo-intelligence/<site>/competitor-sitemaps.json` (not committed); only differences go to
the knowledge base.

## Execution steps

1. Run the command. The first run per competitor is a baseline (no changes reported).
2. Report per competitor: new pages on your keywords, topics you do not cover, refresh waves.
3. Decide per topic with the owner: extend an existing page, plan a better page
   (`seo-content-strategy`) or ignore. Never copy competitor content.

## Analysis methodology

- New/removed/updated come from sitemap URL sets and `<lastmod>`; a site without lastmod shows no
  updates (said in the notes).
- Keyword match: all words of a keyword appear in the URL path; "not covered": slug-word overlap
  with every page of the site below one third.

## Output format

Table per competitor; keys `competitor-new-topics:<domain>`, `competitor-refresh-wave:<domain>`.

## Quality checks

Report what the sitemap shows; a URL in a sitemap is not proof the page ranks.

## Error handling

Unreachable or invalid sitemaps are listed in the notes; that competitor claims no recheck.

## Website-specific configuration

`competitors`, `competitor_watch.max_urls_per_site`, `competitor_watch.max_title_fetches`.

## How findings are stored

Ledger findings, `insights/competitor-watch-<date>.json`, the `competitor-watch` execution.

## Improving this skill

Through proposals (`seo-learning-and-memory`) and `/seo-update-skills`.
