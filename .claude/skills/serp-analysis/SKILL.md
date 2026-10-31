---
name: serp-analysis
description: "Analyze search result data without overstating ranking signals. Planning guidance only; AURORA provider integrations are not implemented yet."
---

# serp-analysis

**Description:** Analyze search result data without overstating ranking signals.

## Purpose
Interpret SERP composition and likely search intent from an authorized provider.

## When to use it
Use for a query, market, or competitor analysis when current SERP data is
available from a configured provider.

## Required inputs
- Query and locale.
- Retrieval date and provider/source metadata.
- A bounded set of result fields or provider results.

## Execution workflow
1. Confirm query intent and locale.
2. Examine result types, recurring topics, and visible features.
3. Compare result patterns and distinguish observation from inference.
4. Identify content gaps as hypotheses, not guaranteed ranking opportunities.
5. Include data coverage and freshness caveats.

## Expected outputs
Intent hypothesis, result-pattern summary, observed features, content
opportunities, and limitations.

## Error handling
Report empty, incomplete, stale, or unavailable SERP data and stop claims that
depend on missing results.

## Quality requirements
Do not promise rankings or infer private analytics from public results. Cite
provider and retrieval date and avoid unsupported search-volume claims.
