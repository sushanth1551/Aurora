---
name: knowledge-graph
description: "Model and maintain provenance-aware research graph data. Planning guidance only; AURORA provider integrations are not implemented yet."
---

# knowledge-graph

**Description:** Model and maintain provenance-aware research graph data.

## Purpose
Represent entities, claims, sources, and typed relationships with traceability.

## When to use it
Use when designing graph schema, validating extracted records, or planning a
Neo4j graph update.

## Required inputs
- Entity and relationship candidates.
- Source identifiers and supporting evidence.
- Entity resolution and data-retention rules.

## Execution workflow
1. Normalize entities and identify candidate duplicates.
2. Validate permitted labels, relationship types, and required properties.
3. Require provenance for factual claims and evidence-bearing edges.
4. Propose idempotent writes and constraints.
5. Review conflicts and preserve alternate source assertions.

## Expected outputs
Validated graph records, relationship rationale, provenance links, and
unresolved entity-resolution cases.

## Error handling
Reject malformed or unsupported records; report duplicates and conflicts rather
than silently overwriting graph facts.

## Quality requirements
Use stable identifiers, parameterized Cypher, least-privilege access, and
evidence provenance. Never store secrets in graph properties.
