# Entity resolution for the graph

Module 3 already merges duplicates *within* a job and gives entities stable UUIDv5 ids from
(type, website, normalized name). `backend/app/graph/entity_resolver.py` maps entities from
**different jobs** onto graph nodes. Each decision is stored in `graph_entity_resolutions`
(job, entity key, graph node id, stage, decision, confidence, reasons, review candidate), so merges
are auditable. They are also reversible: no PostgreSQL entity row is changed, and a resync rebuilds
the mapping.

| Stage | Rule | Confidence |
| --- | --- | --- |
| 1 `stable_id` | entity key resolved in an earlier sync -> same node | 1.0 |
| 2 `normalized` | same label, scope and match key as a known node | 0.98 |
| 3 `domain` | company on a **verified official** domain -> `uuid5(company:<domain>)` | 0.95 |
| 4 `alias` | match key equals an alias key of a node in the same scope | 0.9 |
| 5 `fuzzy` | difflib ratio on match keys, same scope: >= 0.92 merge; 0.80-0.92 review only | ratio x 0.95 |
| 6 `llm` (off) | only for review candidates; merges only if `same_entity`, confidence >= `GRAPH_LLM_RESOLUTION_MIN_CONFIDENCE` (0.9) **and** at least one cited claim exists | model value |

Scope means the owning website's registrable domain. Technology, Integration and Topic are
scope-free shared concepts.

**Never merged automatically:**
- companies on different domains with the same name (decision `review`, both nodes kept);
- products or features with similar names on different websites (another company's product is a
  different entity);
- anything below the fuzzy merge threshold.

Review candidates are listed at `GET /api/v1/graph/review`.

**Merge behaviour:** aliases, original names, `source_record_ids`, source URLs and job ids are
unioned; the strongest verification status is kept; existing properties win and missing ones are
filled; existing relationships are untouched (upserts only add).

**Normalization:** `normalized_name` = NFKC + casefold + collapsed whitespace, with punctuation kept
("C++", "Node.js"). `match_key` = Module 3's identity form (legal suffixes and punctuation removed,
technology aliases) and is used only for identity. Domains are reduced to the registrable domain
(IDNA, `www.` stripped); URLs are keyed by host and path.

No embeddings are used (Module 6 may add a semantic matcher behind the same interface).
