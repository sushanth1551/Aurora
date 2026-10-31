# Answer observation contract

Read when importing captures or comparing snapshots. Input is a JSON object with
`schemaVersion: 1`, `brand`, `hosts` (exact hostnames), `protocol` and `observations`.
The protocol identifies a fixed collection procedure, including device, account/
personalization state, fresh-session policy, search setting and model identification.
Never put credentials or personal account data into it. A changed procedure needs a
new protocol identifier. Unknown model versions must be disclosed in interpretation.

Each observation requires `query`, `platform`, `model`, `language`, `location`,
`surface`, `trial`, `observedAt` (ISO UTC timestamp), `source` (capture identifier),
`outcome` (`answer`, `no_answer` or `unavailable`), `brandMentioned` (boolean or null)
and `citations` (array of absolute HTTP(S) URLs or null).

- A false mention means the complete answer was checked for this brand, including
  relevant aliases. Null means unchecked or unavailable; never substitute false.
- An empty citation array means the full citation list was inspected and empty.
  A cropped or hidden list is null, even if no links appear in the visible fragment.
- `no_answer` records a verified absence of an answer surface; `unavailable` means
  collection failed. Both require null mention and citations and are counted
  separately, outside answer-based rates.
- Preserve original URL strings and captures. Only host matching ignores hostname
  case; subdomains must be listed explicitly. A mention is not a citation, and a
  citation is not an endorsement or proof of factual correctness.
- `trial` is a preassigned repeat slot, not a timestamp. The script rejects duplicate
  query/platform/model/language/location/surface/trial contexts within a snapshot.
  Do not invent new trial IDs to double-count the same capture.

Example (synthetic):

```json
{
  "schemaVersion": 1,
  "brand": "Example",
  "hosts": ["example.com"],
  "protocol": "desktop-fresh-session-search-on-v1",
  "observations": [{
    "query": "Which garden planner should I use?",
    "platform": "answer-engine",
    "model": "reported-model-version",
    "language": "en",
    "location": "GB",
    "surface": "search-answer",
    "trial": "1",
    "observedAt": "2026-09-01T12:00:00Z",
    "source": "capture-001",
    "outcome": "answer",
    "brandMentioned": true,
    "citations": ["https://example.com/planner"]
  }]
}
```

Run `node scripts/ai-observations.mjs --file snapshot.json` or
`node scripts/ai-observations.mjs --before before.json --after after.json`.

Summaries report counts, known denominators and null rates when nothing was measured,
stratified by platform/model/language/location/surface. Comparison requires matching
brand, host set and protocol; exact contexts pair across snapshots and later dates
must follow earlier ones. Deltas use only pairs where both sides measured the metric,
with separate mention and citation denominators. Unmatched contexts are reported,
never silently included in before/after rates. Do not generalize convenience samples
to market share or attribute a change to an intervention without further evidence.
The script validates declarations; it does not open captures or query platforms.
