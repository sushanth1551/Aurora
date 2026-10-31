# Knowledge graph schema

## Status
The graph vocabulary constants are an initial proposal. No Neo4j connection,
constraints, ingestion, or query implementation exists.

## Proposed node labels
- `Company`: normalized organization identity and known names.
- `Person`: public professional identity where collection is appropriate.
- `Product`: product or service associated with an organization.
- `Source`: URL, publisher, retrieval time, and content fingerprint.
- `Claim`: atomic statement, extraction time, and verification state.

## Proposed relationships
- `(Source)-[:MENTIONS]->(Company|Person|Product)`
- `(Company)-[:PRODUCES]->(Product)`
- `(Person)-[:WORKS_FOR]->(Company)`
- `(Claim)-[:SUPPORTED_BY]->(Source)`

Relationships and claims must carry provenance and confidence where useful.
Avoid representing uncertain or time-sensitive relations as permanent truth;
include observed/valid timestamps and retain source-specific assertions.

## Constraints and ingestion
Use stable external/canonical IDs and uniqueness constraints before ingestion.
Resolve aliases conservatively and preserve competing sources. Use parameterized
Cypher and explicit allowlists for labels/relationship types. Validate
relationships before writes and design ingestion to be idempotent.
