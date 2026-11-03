---
name: seo-content-optimization
description: "SEO Intelligence content mode for a configured website: diagnoses existing pages or posts and proposes concrete improvements to titles, meta descriptions, headings, structure, internal links, FAQs and freshness, checked for intent fit, factual accuracy, originality and the site's editorial rules; drafts content only when explicitly requested; implements changes in the site repository only on a branch with the owner's approval. Use for /seo-content-optimization or 'improve these pages/posts'. Do NOT use to publish or deploy."
user-invocable: true
argument-hint: "[site-id] [urls, slugs or 'from-findings']"
metadata:
  author: AURORA
  version: "1.0.0"
  category: seo-intelligence
---

# /seo-content-optimization

## When to activate

`/seo-content-optimization`, "optimise these posts", "fix the titles", "improve the pages outside
the top 10", or acting on open content findings.

## Required inputs

- Website id and targets: URLs/slugs, or `from-findings` (open findings in categories
  `content/*` and `on-page/*`, highest priority first, at most 10 pages per run).
- Search Console page/query data when available (positions decide how boldly to edit).

## Tools and integrations

- `website-seo-discovery` for the live state; read-only repository reads for the source.
- Search Console through AURORA (`/api/v1/gsc`) or supplied exports.
- Writing to the site repository is allowed only after the owner approves the specific change
  list, only on a new branch named `seo/<execution-id>`, never on the production branch, and never
  pushed or deployed by this skill.

## Execution steps

1. Load knowledge (`seo-learning-and-memory`), including the config's editorial rules and every
   rejected recommendation; `start` with mode `content-optimization`.
2. Diagnose each target (`seo-toolkit-page-audit`): intent, title/description length and
   placement of the main query, H1/H2s, answer-first sections, facts and dates, FAQ visibility vs
   schema, internal links in and out, images.
3. Propose edits (`seo-toolkit-content-optimizer`): exact new title and description, heading
   changes, sections to add (with sources), FAQs that answer real queries, 4-8 natural internal
   links. Use ranking tiers if the config defines them.
4. Check quality (`seo-quality-audit`): no stuffing, no repeated boilerplate across pages,
   facts traceable to sources, house style, no invented people or outcomes.
5. New page needed? Write a brief (`seo-toolkit-content-brief`). Draft only if the user asked
   (`seo-blog-writer`).
6. Show the change list and ask for approval. If approved: branch, edit, build/type-check as the
   repository requires, commit with a clear message, and record `seo_change` entries with commit
   hashes; set findings to `implemented` (not verified).
7. Store and report; list reindex requests the owner should make after deployment.

## Analysis methodology

- Preserve intent: a title rewrite keeps the query the page already earns impressions for.
- Change a few high-traffic pages at a time so effects can be attributed.
- Extend the owner page rather than creating a competing page.

## Output format

Per page: current vs proposed (title, description, headings, sections, links), reason, evidence,
risk, verification method; then the approval request or the implemented commits.

## Quality checks

- Lengths within the config's limits; editorial rules applied; sources cited for every new fact.
- Duplicate-content check across the edited pages.
- Nothing pushed, deployed or published.

## Error handling

Build or type-check fails: fix or revert on the branch; never commit a broken build. Unclear facts:
leave them out and list them as questions for the owner.

## Website-specific configuration

Editorial rules, limits, repository paths and production branch come from the website config.

## How findings are stored

Proposed edits as findings or changes; implemented edits as `seo_change` records with commit
references; rejected proposals through `reject` with the owner's reason.

## Improving this skill

Patterns such as "title changes on strong pages caused drops" become proposals through
`seo-learning-and-memory`, backed by performance reviews.
