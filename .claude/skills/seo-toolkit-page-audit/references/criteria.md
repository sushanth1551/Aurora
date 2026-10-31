# Page audit criteria

Load this when deciding what actually matters on a page.

## Start from the job, not the checklist

A page audit that lists every deviation from convention is noise. The first question
is: **what is this page for, who is it for, and what query does it expect to answer?**
Every finding is then a gap between that intent and what the page does.

A page can violate every convention and still be the best answer. A page can satisfy
every convention and still be useless. Convention violations are hypotheses, not
findings.

## Intent mismatch is the highest-value finding

Most page-level SEO failures are not missing tags. They are a page built for one
intent competing for another: a product page where searchers want a comparison, a
blog post where searchers want a tool, a category page where searchers want a
specific item.

If intent is mismatched, metadata fixes are cosmetic. Say so, and lead with it.

## What convention is actually for

| Element | What it does | The real failure | The myth to avoid |
| --- | --- | --- | --- |
| Title | Sets the result's promise and disambiguates from siblings | Duplicated across pages, or promising something the page does not deliver | A universal character limit; truncation depends on rendering, and a truncated but accurate title beats a padded one |
| Meta description | Influences the snippet, not ranking | Missing on pages where the auto-snippet is poor; duplicated | That it must always be written, or must contain the keyword |
| H1 | States what the page is | Absent, or describing something other than the page | That exactly one H1 is a ranking requirement; the real issue is a confusing document outline |
| Headings | Structure the document | Used for styling, so the outline is meaningless | That a keyword must appear in every heading |
| URL | Stable identifier | Instability, and unbounded parameters | That keywords in URLs are a meaningful lever |
| Word count | Nothing | Content too thin to answer the question | Any target number |
| Images `alt` | Accessibility first, image search second | Missing on informative images; decorative images described | Stuffing keywords into `alt` |

## Thin, duplicate and unhelpful

Thin is not short. A page that answers the question completely in 80 words is not
thin. Thin means the page does not do the job it exists for. Judge against the job,
never against a length.

Duplicate is not similar wording. Judge whether a user arriving at either page would
be equally served, and whether the site intended both to exist.

## Prioritising

Rank by: intent mismatch first, then indexability faults that prevent the page from
being eligible at all, then things that materially change what a user or crawler
understands, then snippet-level presentation, then everything else.

Cap the output. A prioritised list of five things that matter beats thirty
observations. Say what you deliberately left out.

## What you can and cannot see

Distinguish a signal that is **missing** from one that was **not observable**. A
canonical absent from raw HTML may be injected client-side; a page fetched without
JavaScript execution cannot settle it. Say which you did, and mark the rest
`not_measured` rather than reporting an absence you did not verify.
