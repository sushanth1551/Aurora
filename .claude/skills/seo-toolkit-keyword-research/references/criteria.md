# Keyword research criteria

Load this when clustering queries or deciding whether a query deserves its own page.

## Cluster by intent, not by string similarity

Two queries belong on the same page when **the same page would satisfy both
searchers**. That is the only test. String similarity is a way of generating
candidates, not of deciding.

Consequences worth stating:
- "cheap running shoes" and "running shoes under 50" are one intent.
- "running shoes" and "how to choose running shoes" are usually two, because one
  wants to buy and one wants to decide.
- "python" as a language and as an animal are the same string and different topics;
  ambiguous head terms must be split by meaning, not merged by spelling.

Where result data exists, overlap in the actual results is the strongest available
signal that two queries share intent. Where it does not, say the clustering is
`inferred` from language and reasoning.

## Intent classes, and the one people get wrong

Informational, commercial investigation, transactional, navigational. The one
routinely mislabelled is **commercial investigation** — "best X", "X vs Y", "X
review". It is neither purely informational nor ready to transact, and pages built
for the wrong side of it underperform. When intent is genuinely mixed or the head
term is ambiguous, say so rather than forcing a class.

Modifiers carry intent reliably: comparison words, purchase words, problem words,
location and time words, and question forms. Use them as evidence, not as rules.

## When a query deserves its own page

It does when a searcher would be **worse served** by being sent to an existing page:
the answer is materially different, the decision stage is different, or the existing
page cannot cover it without losing focus.

It does not merely because the query has volume, because a competitor has a page, or
because a tool grouped it separately. Defaulting to a new page per query is how sites
build cannibalization and thin content at the same time.

Prefer, in order: strengthen an existing page → add a section to an existing page →
create a new page → create a cluster with a hub.

## Demand without a data source

If no volume, CPC or difficulty data is supplied, **do not produce numbers**. Say
demand is `not_available` and work qualitatively: relative demand can be reasoned
about from specificity, breadth of language, modifier patterns and market knowledge,
and must be labelled `estimated_qualitative`.

A ranked list with no numbers and honest reasoning is more useful than invented
figures, and it is the only acceptable option.

## What to hand over

The output is a mapping, not a list: each cluster with its intent, the page that
should own it, whether that page exists, and why the queries belong together. A
cluster without a target page is unfinished work.
