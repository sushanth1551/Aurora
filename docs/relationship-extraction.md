# Relationship extraction and validation

`backend/app/graph/relationship_extractor.py` maps stored claims to relationships
deterministically. No LLM is involved.

| Claim predicate | Relationship | Assertion | Notes |
| --- | --- | --- | --- |
| `offers_product` | Company OFFERS Product | direct | |
| `offers_service` | Company PROVIDES Service | direct | |
| `feature` | Product HAS_FEATURE Feature | direct | company-level feature claims are rejected by the registry |
| `integration` | Product/Company INTEGRATES_WITH Integration | direct | |
| `mentions_technology` | Product/Company USES Technology | **inferred** | basis: "technology mentioned on the subject's page"; a mention is not proof of use |
| `industry` | Company OPERATES_IN Topic | direct | |
| two companies in one job with verified industry claims naming the same topic | Company COMPETES_WITH Company | **inferred** | `verification_status = inferred`, confidence 0.3, `basis = same_industry:<topic>`, both claims cited |

Other predicates (headquarters, founded year, description, price, target customer, use case,
deliverable, trial) stay as claims. Headquarters, founded year, industry and description also become
Company properties when the claims are supported and do not conflict. Conflicting values are listed
in `conflicting_properties` instead.

**Validation (every row):**
1. Subject and object entities must exist after resolution.
2. Claims with status `unsupported` (excerpt not found in the stored page) create **no** fact edge.
3. `check_relationship()` enforces the registry: known type, allowed source and target labels,
   direction, same-type for COMPETES_WITH / RELATED_TO.
4. Duplicates of the same edge are merged into one row that keeps every claim, evidence id and
   source; symmetric edges are stored once with ordered ids.

Rejected rows are returned (and stored, at most 200 per run, in `graph_sync_runs.rejected`) with a
reason and claim id.

**Future extractors** (e.g. LLM) must emit `RelationshipProposal`s, which go through
`validate_proposals()`: the same registry checks apply, every cited claim must exist, evidence-required
types without a real claim are rejected, and only `inferable` types may be `inferred`.

Not produced yet (no data source): Product COMPETES_WITH Product, RELATED_TO, Keyword BELONGS_TO,
Article TARGETS / COVERS / LINKS_TO / CITES. Their schema, registry entries and constraints exist
for Modules 6-7.
