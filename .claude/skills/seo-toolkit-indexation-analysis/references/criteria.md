# Indexation analysis criteria

Load this when interpreting URL-state datasets.

## Know what each source can and cannot prove

| Source | Proves | Does not prove |
| --- | --- | --- |
| Crawler export | What a crawler saw at that moment, from its seeds | Anything about indexing |
| Sitemap | What the site *claims* should exist | That those URLs exist, are reachable or are indexed |
| Coverage report | The provider's own classification | The exact live state; it lags and it samples |
| URL inspection | State for one URL at one time | Anything site-wide |
| Server logs | What was actually requested and served | Intent, or indexing |
| `site:` query | Almost nothing usable | **Never an index count.** It is an estimate that varies between runs |

Conflicts between sources are the finding, not an error to smooth over. A URL in the
sitemap, absent from the crawl, and reported as excluded is a coherent story worth
telling.

## Compare like with like

A diff is only meaningful if both snapshots were produced the same way. Different
crawl depth, different seed set, different limits, different user agent, or a
different date all manufacture false changes. Establish comparability first, and if
you cannot, say the diff is `inferred` and bound the claim.

Normalise before diffing: protocol, host, trailing slash, case, parameters, fragments.
Most spurious "disappeared URL" findings are normalisation artefacts.

## Group before counting

A list of 4,000 changed URLs is unusable. A statement that one template lost its
`noindex` and another gained it is actionable. Group by path pattern, template,
directory, parameter presence and status, then report per group with counts drawn
only from the supplied data.

## What is worth flagging

- URLs that disappeared from the crawl but remain in the sitemap.
- Growth that has no corresponding content event — the signature of generated or
  parameterised bloat.
- `noindex` appearing on a group that previously did not have it, especially after a
  deployment.
- Canonical targets that changed, or now point at non-indexable or missing URLs.
- Sitemap entries that are non-200, redirecting, canonicalised elsewhere or noindexed.
  A sitemap should list canonical, indexable, 200 URLs.
- Orphans: present in the sitemap or logs but not reachable in the crawl.
- Status changes concentrated in one pattern.

## Expected versus unexpected

Ask what changed on the site in the window before calling anything an anomaly. A
migration, a release, a seasonal catalogue change or a deliberate pruning all produce
alarming diffs that are correct. Without deployment context, mark the classification
`inferred` and ask for it.

## Limits to state every time

Indexation state is owned by the search provider and is not fully observable. Say
what your evidence covers, name the source that could confirm the actual state, and
never present a count derived from a crawl or a `site:` query as an index count.
