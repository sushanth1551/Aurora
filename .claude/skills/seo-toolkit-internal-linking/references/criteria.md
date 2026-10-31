# Internal linking criteria

Load this when deciding whether a link should exist.

## The test for a proposed link

A link is justified when **a reader at this point in this page would plausibly want
that target next**. That is the whole test. Everything else is a way of finding
candidates, not of justifying them.

Explicitly not justifications: both pages mention the same keyword; the target needs
links; the target is important; a tool suggested it; the pages are in the same
category.

## Where the real problems are

Most sites do not have a "not enough links" problem. They have:

- **Orphans and near-orphans** — business-important pages reachable only from a
  sitemap, or only from one deep page.
- **Depth mismatch** — important pages buried, trivial pages one click from home.
  Depth should track importance, not the CMS hierarchy.
- **Undifferentiated anchors** — hundreds of "read more" or the exact same phrase
  everywhere, so no link says anything about its target.
- **Boilerplate dominance** — nearly all internal links come from navigation and
  footer, so no page expresses a specific relationship to another.
- **Missing hubs** — a topic with twenty pages and no page that organises them.

Diagnose these before proposing individual links.

## Navigation versus contextual links

They do different jobs and should not be counted together. Navigation links express
site structure and appear everywhere; contextual links express a specific
relationship in a specific place. A page with fifty navigation links and zero
contextual links is weakly connected, whatever the raw count says.

When reporting inlink counts, separate the two or the number is misleading.

## Anchors

The anchor should describe the target well enough that a reader knows where they are
going without the surrounding sentence. Vary naturally, because real language varies;
identical anchors sitewide look manufactured and help no one.

Avoid the two opposite myths: that exact-match anchors are always better, and that
they must always be avoided. The question is whether the anchor is accurate and
readable.

## Too many links

The failure is not a count. It is dilution of meaning: when a page links to
everything, no link means anything, and the reader cannot tell what is worth
following. Judge by whether a reader could identify the two or three links that
matter on the page.

## What internal linking cannot do

It cannot rescue a page with no reason to exist, resolve an intent mismatch, or
substitute for content that is missing. Say so when the proposed fix is really a
content or architecture problem.

## Evidence limits

Inlink counts, depth and orphan status are properties of the **whole graph**. They
cannot be inferred from one page. If you only have one page, say the graph
properties are `not_available` and restrict findings to that page's outbound links.
