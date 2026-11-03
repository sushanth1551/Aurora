---
name: seo-quality-audit
description: "Review pages or drafts against explicit SEO and editorial criteria: intent fit, structure, thin or padded content, stale facts and years, unsupported claims, duplication and boilerplate, originality, accessibility basics and the website's own editorial rules. Use on supplied pages, drafts or briefs, and as the content-quality step of /seo-audit and /seo-content-optimization. Separates observable defects from subjective suggestions."
metadata:
  author: AURORA
  version: "2.0.0"
  category: seo-intelligence
---

# seo-quality-audit

**Description:** Review content against explicit SEO and editorial criteria.

## Purpose
Identify actionable content and technical-on-page issues while separating observable checks from
subjective recommendations, and check that proposed content is original, accurate and not
stuffed.

## When to use it
Use on a supplied page, draft, or content brief when audit scope is clear; in `/seo-audit`
(content quality) and `/seo-content-optimization` (before any edit is proposed or made).

## Required inputs
- Content or page URL/text (live page, repository source, or draft).
- Target audience, locale, and primary topic if available.
- Audit criteria or permission to apply a stated baseline rubric; the website config's
  `rules` (limits and editorial rules) always apply.

## Tools and integrations
`seo-data` MCP `audit_page` for word count, headings and alt text; read-only repository reads for
source content; WebFetch of cited primary sources when checking a fact.

## Execution workflow
1. Record scope and input limitations.
2. Check clarity, structure, intent fit, accessibility, and on-page signals.
3. Thin or padded: below the site's minimum word count, or above it only through repetition.
4. Freshness: years, fees, dates and policies; a year in the title must match the data in the body.
5. Check factual claims against supplied evidence when available; flag unsupported superlatives
   ("#1", success rates, student counts) as trust risks.
6. Originality: repeated paragraphs or boilerplate across pages, near-duplicate posts, generic
   AI-style filler, and keyword repetition that reads unnaturally.
7. Apply each editorial rule from the website config and report violations by rule.
8. Rank findings by impact and confidence and propose actionable edits.
9. Distinguish missing data from a detected defect.

## Expected outputs
Prioritized findings (keys like `thin-content:<slug>`, `stale-year:<slug>`,
`unsupported-claim:<path>`, `editorial-rule:<rule>:<slug>`), evidence or examples,
severity/confidence, and practical recommendations.

## Quality requirements
Avoid keyword-density prescriptions and ranking guarantees. Keep findings specific, reproducible,
and proportionate to evidence. Quote at most a short excerpt as evidence.

## Error handling
Report inaccessible pages or absent metadata; do not infer technical findings from page copy
alone. If a fact cannot be checked, say "unverified" instead of calling it wrong.

## Website-specific configuration
Limits and editorial rules come from the website config (`rules`), never from this file.

## How findings are stored
As findings via `seo-learning-and-memory`, with `checked.categories` such as `content/thin`,
`content/freshness`, `content/claims`, `content/editorial`.

## Improving this skill
Propose new checks through `seo-learning-and-memory` when the same quality problem is found in
several runs; apply only after approval.
