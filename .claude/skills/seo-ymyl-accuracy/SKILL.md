---
name: seo-ymyl-accuracy
description: "SEO Intelligence accuracy checks for health and medical-education content (YMYL): fees, percentages and pass rates without an official source nearby (usmle.org, nbme.org, ecfmg.org, fsmb.org, nrmp.org ...), past years presented as current, unsupported claims ('#1', 'guaranteed', '100%', 'success rate') with negations ignored, and an owner-verified facts registry that flags pages contradicting a verified fact and facts due for re-verification. Use for /seo-ymyl-accuracy, 'are my facts correct', 'outdated fees', 'E-E-A-T trust', 'claims on my site'."
user-invocable: true
argument-hint: "[site-id]"
metadata:
  author: AURORA
  version: "1.0.0"
  category: seo-intelligence
---

# /seo-ymyl-accuracy

## When to activate

"Are my fees/dates correct", "outdated information", "unsupported claims", "trust/E-E-A-T", before
publishing a fact-heavy post, or as the accuracy step of `/seo-audit` and
`/seo-content-optimization`.

## Required inputs

Website id; `ymyl` section of the site config (authoritative domains, claim terms, facts).

## Tools and integrations

Runs inside the crawl: `scripts/seo_insights.py crawl <site>` (use `--no-video --no-source-map`
for an accuracy-only run). For one draft, read the text and apply the same checks.

## Execution steps

1. Run the crawl (or read its latest report).
2. Report per page: unsourced numbers, stale years, claims, fact mismatches; then facts due for
   review.
3. To add a fact to the registry, the owner (or you, with the owner's confirmation) checks the
   official page; record `id`, `statement`, `pattern` (regex with one group for the value),
   `expected`, `source_url`, `verified_on`, `review_after_days` in the site config. Never add a
   fact from memory or from a third-party site.

## Analysis methodology

- Unsourced: a paragraph, list item or table cell with money, a percentage or a pass rate and no
  authoritative link in the block or its section. Teaser cards are skipped; pages with an
  official link elsewhere get lower priority.
- Stale: a past year in the title or a heading (not on listing pages), or "in/as of/latest
  <past year>" next to exam or fee words. The action asks to check for newer official data;
  the year may already be the latest available.
- Claims: configured terms, skipping negations ("no score guarantees a match").

## Output format

Per page, with the exact sentence; keys `unsourced-numbers:<path>|blog`, `stale-year:<path>|blog`,
`unsupported-claim:<path>|blog`, `fact-mismatch:<fact>:<path>`, `fact-review:<fact>`.

## Quality checks

Report the sentence, not a paraphrase; never "correct" a number without the official source.

## Error handling

Pages not fetched are not judged.

## Website-specific configuration

`ymyl.authoritative_domains`, `ymyl.claim_terms`, `ymyl.facts`, `rules.editorial`.

## How findings are stored

Ledger findings via the crawl execution.

## Improving this skill

Through proposals (`seo-learning-and-memory`) and `/seo-update-skills`.
