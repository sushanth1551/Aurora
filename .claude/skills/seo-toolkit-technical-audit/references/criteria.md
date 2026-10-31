# Technical audit criteria

Load this when deciding whether a technical observation is actually a problem.

## Triage: what to establish first

Technical findings are worthless without scope. Before diagnosing anything, fix:

- **The URL population.** What is the site supposed to contain? A crawl of 200,000
  URLs where the catalogue has 8,000 is itself the finding.
- **The sample.** What did you actually observe, and what did you not? Every finding
  inherits this boundary.
- **The environment and date.** Staging, CDN-cached and logged-in views differ.

## The order that finds real problems fastest

Work outside-in. Each layer is pointless if the one above it fails:

1. **Reachable** — does the URL respond, and with what status?
2. **Permitted** — does robots.txt allow fetching it?
3. **Indexable** — meta robots, `X-Robots-Tag`, canonical target.
4. **Renderable** — does the meaningful content exist in what a crawler receives?
5. **Discoverable** — is it linked from somewhere that gets crawled?
6. **Distinct** — is it the canonical answer, or one of many near-identical URLs?
7. **Delivered well** — Core Web Vitals and delivery performance, only when measured.

A page failing at layer 2 makes every finding below it irrelevant. Report the
highest-layer failure and say the lower layers are `not_measured`.

## Distinguishing a real problem from a normal state

| Observation | Not a problem when | Problem when |
| --- | --- | --- |
| `noindex` | Deliberate on thin, internal or duplicate pages | On pages the business expects to rank, or applied by an unintended template rule |
| Redirect | Single hop, permanent, relevant target | Chains, loops, temporary for a permanent move, or an irrelevant target |
| Canonical to another URL | Genuine duplicate or parameter variant | Points at a non-indexable, 404, redirecting, or unrelated page; or applied site-wide to one URL |
| Parameters in URLs | Bounded, canonicalised set | Unbounded combinations generating infinite crawl space |
| Faceted navigation | Crawlable set is deliberately limited | Every facet combination is linked and crawlable |
| Blocked in robots | Genuinely worthless paths | Blocking resources needed for rendering, or blocking pages that should instead be `noindex` |
| Orphan page | Deliberately unlinked (campaign, transactional) | Business-important page with no internal path |
| Slow page | Measured and within the site's own norms | Measured, and a critical template is materially worse |

**The single most common mistake** is treating `robots.txt` disallow as a way to
deindex. It prevents fetching, not indexing, and it prevents the crawler from ever
seeing a `noindex`. If the goal is removal from results, the page must be fetchable
and carry `noindex`.

## Crawl traps

Look for unbounded generative patterns rather than counting URLs: calendars with
infinite next-month links, filter combinations, session or tracking parameters
producing distinct URLs, relative-path bugs producing infinitely nesting paths,
search-results pages that are crawlable and link to more search-results pages.

The signal is a URL pattern whose cardinality has no upper bound, not a large number.

## Rendering

Ask what a crawler receives, not what a browser eventually shows. Meaningful content
that appears only after client-side execution, after user interaction, or behind a
loading state is at risk. Content behind a click is not indexed as page content.

State clearly which you observed: raw server response, or rendered DOM. They are
different evidence, and conflating them is a common error.

## Core Web Vitals and delivery

Only report these when measured, with the source and whether it is field or lab
data named. Field data reflects real users and is the outcome; lab data is
diagnostic and reproducible. Never mix them in one claim, and never state a metric
that was not supplied or measured. Without data, this layer is `not_measured` — not
"probably fine" and not "likely slow".

## Hreflang

Errors here are mechanical and high-frequency: missing return links, self-reference
absent, language or region codes invalid, annotations pointing at redirecting or
non-canonical URLs, mismatch between the hreflang target and the canonical. A cluster
is only as good as its weakest reciprocal link. Verify both directions.

## Severity

Rank by affected scope weighted by page importance, then by reversibility, then by
whether the issue blocks a higher layer for other pages. A site-wide indexability
fault outranks a large number of minor metadata issues, however impressive the count.
Never rank by issue count.
