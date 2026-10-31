# Programmatic SEO decision criteria

Load this before endorsing or rejecting a scaled page set.

## The justification test

A programmatic set is justified only when **all five** hold. Any one failing means
the answer is no, or a smaller set.

1. **Demand exists per page, not per pattern.** "People search for X in cities" is
   pattern-level. The question is whether the long tail of individual combinations
   has searchers. Most combinatorial sets are mostly dead combinations.
2. **Each page answers something a general page cannot.** If the user is equally
   well served by a filter on one page, build the filter.
3. **The data is sufficient, correct and fresh** for every page, not the demo page.
   The failure mode is a template that looks excellent for the top 20 rows and empty
   for the remaining 20,000.
4. **The pages differ in substance, not only in a variable.** Swapping a noun into
   the same sentences produces near-duplicates regardless of how many words result.
5. **Indexation can be controlled.** You can decide, before launch, which pages are
   allowed to be indexed and can change that decision later.

## Sizing honestly

State the combinatorial arithmetic explicitly: `entities × attributes = pages`. Then
apply the qualifying filters and state how many survive. A proposal that goes from
40,000 to 900 after applying a minimum-data rule is a good outcome, not a failure —
say so in those terms.

Watch for multiplicative dimensions. Two dimensions is usually manageable, three is
usually an explosion. If the proposal has three or more, ask which one is really a
filter rather than a page.

## Distinguishing a useful set from a doorway

Doorway characteristics: pages exist to capture a query rather than to serve a need;
the substantive content is identical across pages; users landing on one would be
routed elsewhere to actually accomplish anything; the set would not be built if
search did not exist.

Useful-set characteristics: each page holds data or content that a user would
genuinely want on that page; the page completes a task; the set would still be
worth building for a user arriving from a link.

The honest question: **would you build these pages if search engines did not exist?**
If not, the set needs a stronger justification than traffic.

## Cost of getting it wrong

Scaled low-value pages are not neutral. They consume crawl capacity that established
pages need, dilute the site's topical clarity, and are slow and expensive to unwind
once indexed. The asymmetry matters: a pilot that is too small costs a delay; a
launch that is too large costs months of cleanup. Recommend the pilot.

## Pilot design

Before scale, ship a bounded slice and define in advance what success means:

- A sample spanning the best, median and worst data quality — not just the best.
- A stated observation window, acknowledging that indexing and evaluation take time
  that is not controllable.
- Success and rollback criteria written before launch, in terms of the site's own
  baseline, never as invented numbers.
- A decision owner and a date on which the decision is actually made.

## Architecture and index control

- Inclusion rule: the minimum data a page must have to exist at all.
- Exclusion: pages below that bar are not generated, rather than generated and hidden.
- Discovery: real internal links from relevant hubs, not only sitemap entries.
- Canonical: self-referencing; identify near-duplicate axes early.
- Sitemaps: segmented so indexation can be observed per group.
- Monitoring: per-group, so a failing segment is visible without a full audit.

## What never justifies the set

Competitor page counts. Template availability. Available data volume. A traffic
projection derived from multiplying an assumed per-page number by the page count —
that number is fabricated and must not appear.
