# Search performance analysis criteria

Load this when explaining a change in search performance data.

## Validate the data before believing it

Most wrong conclusions come from the dataset, not the analysis. Before comparing
anything, establish: the date range and timezone, whether the periods are comparable
in length and seasonality, filters applied, whether rows are sampled or truncated,
whether the export is complete for the period, and the attribution and deduplication
rules of the source.

Two specific traps: recent days are often incomplete, which manufactures a decline;
and a source that caps returned rows will show a "drop" that is really truncation.

## Decompose before explaining

A site-level change is almost never a site-level cause. Split it until the change
localises:

- by page group and template,
- by query group and intent,
- by device, market and language,
- by search appearance where available,
- by new versus returning URLs.

If the change is concentrated in one segment, the explanation lives there. If it is
uniform across every segment, the cause is more likely site-wide, seasonal, or a
measurement change.

## Separate the arithmetic

Clicks move because impressions moved, or because CTR moved, or both. Impressions
move because of eligibility or demand. CTR moves because of position, snippet, or
what else is on the result page. Say which factor moved before proposing a cause —
this alone eliminates most wrong explanations.

Average position is an average over a changing set of queries. A "worsening" average
is often new queries entering at low positions, which is growth, not decline. Never
interpret it without the impression set behind it.

## Candidate causes, checked rather than assumed

Deployment and content changes in the window; indexation changes; seasonality and
known calendar effects; result-page composition changes; tracking, property or
filter changes; demand shifts; and provider-side changes. Check the ones you can
check with the evidence available, and name the ones you cannot as `not_measured`.

Correlation with a deploy date is a hypothesis. Say what would confirm it.

## What must never be produced

A number that was not in the data. No projected traffic, no expected CTR uplift, no
predicted position change, no "this is worth X". Impact stays qualitative and
labelled as such, and the baseline is named or marked `not_available`.

Equally: do not declare a change significant without a stated basis. If you have no
basis for significance, describe the magnitude and say significance is
`not_measured`.

## The useful output

The most valuable result is often a ranked list of competing explanations with the
evidence for and against each, plus the single next measurement that would
discriminate between them. That is more useful than a confident single cause.
