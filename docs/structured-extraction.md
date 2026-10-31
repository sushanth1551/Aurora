# Structured extraction and entity resolution

## Principles

1. **Only what the page says.** Every fact is extracted from an explicit
   statement and carries a verbatim `evidence_excerpt` from the processed page.
   An excerpt that cannot be found in the page text is discarded at extraction
   time and re-checked later by the evidence manager.
2. **No inference.** Headquarters, founding year, company size and prices are
   never inferred; missing fields stay `None`/empty.
3. **Facts vs. observations.** Descriptive/marketing statements are recorded as
   vendor-stated (see `evidence-validation.md`); technology names are recorded as
   `mentions_technology`, not "uses".
4. **Extraction confidence is not factual certainty.** `confidence_score` is how
   sure the extractor is of its pattern; `verification_status` is decided
   separately from the stored source text.
5. **Works without any LLM.** Deterministic extraction is the default.

## Entity schemas (`schemas/intelligence.py`)

`CompanyInfo`, `ProductInfo`, `ServiceInfo`, `TechnologyInfo`, `PricingInfo`,
`FeatureInfo`, `TargetCustomerInfo` (Pydantic v2), each with `source_urls`. They
are wrapped as `ExtractedEntity(entity_type, name, data, source_urls,
scope_domain, confidence)` for resolution.

## What the deterministic extractor reads

| Entity | Source pages | How |
| --- | --- | --- |
| Company | homepage, about, company, contact | description = first substantive paragraph; `headquartered in / head office / headquarters: X`; `founded/established/incorporated ... YYYY`; `N employees/team members`; `is a(n) <phrase> company/platform/provider` -> industry |
| Product | product pages | name = H1 (generic names like "Products" are skipped); description = first paragraph; bullets under headings matching feature/capability/key/benefit -> features; "integrations" -> integrations; "use case" -> use cases |
| Feature | product, features pages | bullets under feature headings; on a features page each H2/H3 (not FAQ/CTA headings) with its first paragraph |
| Service | service pages | name = H1; description; bullets under deliverables/includes/scope |
| Pricing | pricing pages | currency symbol/code + amount (+ `per month/year/user/seat`) in section text, bold plan labels and Markdown tables; plan = nearest heading/bold/first table cell; `free tier`, `N-day free trial`, `contact sales/custom pricing` |
| Technology | any page | word-boundary match against a curated list; evidence = the sentence |
| Target customer | home/about/product/service/features | a fixed vocabulary (enterprises, startups, SMBs, agencies, developers, ...) appearing in a sentence with "for / serving / built for / trusted by ..." |

Pricing details: `price_amount` is a `Decimal`; `currency` is an ISO code when
unambiguous (`EUR`, `GBP`, `INR` from their symbols; `USD`/`EUR`/... when written
as codes) but an ambiguous `$` is kept as `$`; `billing_interval` is only set when stated (`per month/year`, `monthly`,
`annually`); `pricing_model` is `per_user`, `subscription` or
`custom / contact sales`; `source_url` and `last_verified_at` are always set. If
a pricing page is crawled and no price is found, nothing is recorded and the
report says "not publicly found (checked N pricing page(s))".

## Optional LLM extraction (not wired)

`LLMStructuredExtractor` takes an injected `LLMClient` (`complete_json(system,
user) -> str`). It is used only when `WEBSITE_RESEARCH_ENABLE_LLM_EXTRACTION=true`
**and** a client is supplied; today no Groq/Claude adapter is implemented, so
enabling the flag adds a note to the response and deterministic extraction is
used. If a client is supplied:

* the prompt (`prompts/extraction_prompts.py`) wraps page text in
  `<untrusted_source_content>` tags, strips control characters, neutralizes
  delimiter look-alikes, caps the size, tells the model to extract only explicit
  facts, never follow instructions in the content, and never see secrets;
* the reply must be JSON that validates against `LLMClaimBatch` (known entity
  types, predicate pattern, length limits, no extra fields); invalid replies are
  ignored with a note;
* each claim's `evidence_excerpt` must occur in the page text or it is dropped;
* LLM claims are labelled `extraction_method=llm`, get low confidence, and can
  never be better than `partially_supported`.

## Entity resolution (`entity_resolver.py`)

Deterministic and conservative; every decision is explained in `match_reasons`.

* **Normalization** (`normalize_entity_name`): lower-case alphanumerics; company
  names drop legal suffixes (`Inc`, `Ltd`, `Pvt`, ...); technology aliases
  (`Node.js`=`NodeJS`, `Postgres`=`PostgreSQL`, `K8s`=`Kubernetes`, `GCP`); customer
  segment aliases (`SMBs`=`small and medium businesses`).
* **Merge** when the type, website scope and normalized name are identical
  (e.g. three mentions of "Acme Flow"), or for products/features/services when
  names are near-identical (similarity >= 0.92) on the same website.
* **Keep separate** when similarity is 0.80-0.92 ("possible duplicate ... kept
  separate"), and **never merge across websites**: the same product name on two
  companies stays two entities with a note. Technologies are shared concepts and
  merge across websites.
* **Aliases and originals** are preserved (`aliases`, `original_names`); scalar
  conflicts between merged mentions are kept under `attributes._alternatives`.
* **Stable ids**: `entity_id = UUIDv5(type, website scope, normalized name)`, so
  re-running research yields the same identifiers.
* **Pluggable matcher**: `EntityMatcher.similarity(a, b)` lets an embedding model
  replace the default `difflib` ratio later; no vector store is required.
* `link_claims` attaches `entity_id` to claims (pricing/feature claims attach to
  their owning product/service, else the website's company).
