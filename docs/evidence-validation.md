# Evidence and fact validation

`research/evidence_manager.py` decides how well each extracted claim is backed by
the content AURORA actually retrieved. It never uses extractor confidence, never
trusts an LLM, and never treats a search snippet as page evidence.

## The claim (`ResearchClaim`)

`claim_id`, `subject_entity` (+ `subject_type`, `scope_domain`, `entity_id`),
`predicate`, `object_value`, `claim_text`, `source_url`, `source_title`,
`source_domain`, `source_content_hash` (hash of the exact normalized content the
excerpt came from), `source_kind` (official / third_party / unknown),
`evidence_excerpt` (short, verbatim), `extraction_method`
(deterministic / llm), `confidence_score`, `observed_at`, `publication_date`
(only if the page provided one), `verification_status`, `notes`.

## Verification statuses

| Status | Meaning |
| --- | --- |
| `supported` | Excerpt found in the stored page **and** the source is the verified official website **and** the predicate is a factual (not promotional) one |
| `partially_supported` | Excerpt found, but: the source is third-party or its ownership is unverified; or the predicate is a vendor statement (`description`, `industry`, `target_customer`, `use_case`) or a bare mention (`mentions_technology`); or the claim came from an LLM |
| `conflicting` | Different values for the same fact from different evidence; **all** values are kept |
| `unsupported` | The excerpt is **not** in the retrieved page text (extractor/LLM error or fabrication) |
| `unverified` | The cited source content was not retrieved (e.g. only a search snippet) |

The excerpt check ignores case, Markdown emphasis and whitespace; the corpus is
the page's normalized Markdown plus its title and meta description.

## Source kind

`official` only if the source website is `verified` (see `website-discovery.md`);
`third_party` for directory/social/news domains; otherwise `unknown`. Claims on an
unverified site therefore cannot be `supported`.

## Conflict detection

* Single-valued predicates (`headquarters`, `founded_year`, `company_size`):
  different normalized values from the evidence conflict.
* Prices: grouped by plan (`price:<plan>`); different amounts/currencies/intervals
  for the same plan conflict **only across different source pages** (one page can
  legitimately list monthly and annual prices).
* Conflicting claims all become `conflicting`, each keeps its source and
  excerpt, official-source claims get the note "official source; preferred for
  company-owned facts", and a `ClaimConflict(subject, predicate, values,
  claim_ids)` is produced. Reports show the conflict instead of picking a winner.
* Unsupported/unverified claims never take part in conflicts.

## Marketing claims

AURORA records what a website *states*. Statements like "the fastest platform"
are marketing: they would be at best `partially_supported` ("vendor-stated; not
independently verified"). Performance, market-share, revenue and customer-count
claims are not extracted at all.

## Pricing completeness

`pricing_gaps(entities)` lists stated prices missing currency, billing period or
plan name. Pricing entities always carry `source_url` and `last_verified_at`. If
no price is found, nothing is invented: reports say "not publicly found".

## Coverage metrics (`EvidenceCoverage`)

`total_claims`, counts per status, `coverage_ratio` = (supported + partially
supported) / total, `entities_total`, `entities_with_supported_claim`,
`pricing_found`. They are returned by the API (`coverage`), stored in the report
and logged (`evidence_validated`).

## Limits

Verification proves "this text appears on this page"; it does not prove the
statement is true in the world, and deterministic extraction can attach a correct
excerpt to a wrong interpretation. Treat `supported` as "the official site says
so". Publication/modification dates are absent unless the provider returns them.
