# Cannibalization decision criteria

Load this when a page pair looks like a conflict and the recommendation is not obvious.

## The test that matters

Cannibalization is not query overlap. It is **two pages competing to be the answer
to the same job at the same decision stage, where neither has a defensible primary
query family**.

Ask, in order:

1. **Different job?** A category page that helps a user *choose* and a guide that
   helps a user *understand* are not competing, even with heavy query overlap.
   Different job → not cannibalization. Stop.
2. **Different decision stage?** "What is X" and "buy X" serve the same topic at
   different stages. Different stage → not cannibalization, but check that internal
   links move the user forward.
3. **Does each page own a query family it wins on?** If page A is the intended
   answer for a family and page B for another, overlap at the edges is normal and
   healthy. Both own a family → not cannibalization.
4. **Only if none of the above separates them** is this a conflict.

## What is evidence and what is not

| Signal | Weight | Trap |
| --- | --- | --- |
| Same page swapping in results for one query over time | Strong, when observed | Requires real ranking data over a window; never assume it |
| Both pages appearing for the same query | Moderate | Two results from one site is often intended, not a fault |
| Shared target keyword in title | Weak | Titles are an intention signal, not an outcome |
| High text similarity | Weak alone | Similar wording with different purpose is not conflict |
| Both pages linked with the same anchor from the same contexts | Moderate | This is often the actual cause, and the cheapest fix |
| Impressions split across two URLs for one query family | Strong, when supplied | Needs a query-level export, not a page-level total |

Without ranking or query-level data, say so: the diagnosis is `inferred`, not
`observed`, and the recommendation must be reversible.

## Choosing the remedy

Prefer the least destructive action that resolves the conflict.

| Situation | Action | Why |
| --- | --- | --- |
| Different job or stage, weak linking | Change internal linking and anchors | Cheapest, reversible, often sufficient |
| Both useful, overlapping intent at the edges | Differentiate: sharpen each page's promise, title and opening | Keeps both assets |
| One page is a thin subset of the other, no unique value, low or no performance | Consolidate into the stronger page, then 301 | Removes the conflict and keeps equity |
| Near-duplicates by design (print, parameters, syndication) | Canonical | Not a content problem |
| Both perform, both own a family | Do nothing; document why | Avoid fixing a non-problem |
| Legal, regional or contractual reasons for both | Keep, and separate targeting explicitly | Constraint outranks tidiness |

**Never redirect a page that currently performs** on the basis of similarity alone.
The cost of a wrong consolidation is permanent; the cost of waiting is a report.

## Which page survives a consolidation

Rank candidates by: existing performance where data exists, link equity and internal
prominence, depth and uniqueness of content, URL stability and fit with the intended
architecture, and conversion role. State the ranking used. When performance data is
absent, prefer the page that best matches the intent, and say the choice is
`inferred`.

## Common misdiagnoses

- Treating a paginated series, a filtered view or a hub-and-spoke set as conflict.
- Calling a brand and a generic page a conflict when they serve different searchers.
- Consolidating seasonal pages that alternate by design.
- Merging two pages that a site's navigation deliberately separates for usability.
- Diagnosing from a keyword tool export with no ranking or click data at all.
