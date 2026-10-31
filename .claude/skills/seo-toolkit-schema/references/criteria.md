# Structured data criteria

Load this when validating or generating markup.

## The rule that outranks everything else

**Markup describes what is on the page and true in the world. It never creates it.**

If a fact is not present on the page and not supplied by the user, it does not go in
the markup. Not as a placeholder, not as an example value, not as a plausible
default. When a required property is missing, the answer is "this type cannot be
used truthfully yet, here is what is needed" — not invented markup.

This applies hardest to the properties people most want to fabricate: ratings and
review counts, prices and availability, authors and credentials, dates, and business
identity or location details. Fabricating these is misrepresentation, and for review
and rating markup it is the single most commonly penalised structured-data abuse.

## Validity is not eligibility

Three different questions, routinely conflated:

1. **Is it syntactically valid JSON-LD?** Deterministic; a script can answer it.
2. **Is it valid against the vocabulary?** Types and properties exist and are used
   in the right places.
3. **Does it meet a search feature's requirements?** Separate, changeable, owned by
   each search provider, and never guaranteed even when met.

Answer them separately. Never promise a rich result. Feature requirements change;
if current requirements matter and a web capability exists, check them rather than
relying on recalled knowledge, and say when the check was made.

## Diagnosing existing markup

In order:

1. **Parse** — syntax, then whether it is reachable in the delivered response or
   only injected client-side.
2. **Identity** — does `@id` / `url` actually identify this page's entity? Broken
   identity silently merges or splits entities across a site.
3. **Agreement with the page** — every marked-up value visible and matching. A price
   in markup differing from the price on the page is a defect, not a detail.
4. **Type fit** — the type describes what the page actually is, not what would be
   convenient.
5. **Duplication and conflict** — multiple blocks describing the same entity with
   different values; markup from a plugin conflicting with markup from a theme.
6. **Relationships** — `isPartOf`, `mainEntity`, `about`, breadcrumb continuity.
   Most sites get single entities right and relationships wrong.

## Generating markup

Only from supplied or directly observed facts. State explicitly which properties you
filled, which you left out and why. Prefer fewer, correct properties over a complete
template with guessed values. Return the missing-facts list as part of the output —
it is usually the most useful part.

## Common defects worth checking

Marked-up content not visible to users; an entity marked on every page rather than
its own page; breadcrumbs not matching the actual navigation path; `Organization`
duplicated per page instead of referenced; aggregate ratings without underlying
reviews; dates in the wrong format or timezone-ambiguous; leftover markup from a
template describing a different page type.
