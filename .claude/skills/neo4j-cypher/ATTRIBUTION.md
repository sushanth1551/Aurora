# Attribution

- **Source repository:** https://github.com/neo4j-contrib/neo4j-skills
- **Original path:** `neo4j-cypher-skill` (SKILL.md, README.md, `references/`)
- **Commit:** `39247b9c4ab20062aefd1cfe83c040167ef9ee83`
- **Retrieved:** 2026-10-31
- **License:** MIT (LICENSE.txt copied from the repository root; copyright Neo4j Contrib, 2026)
- **Status in AURORA:** adapted

## Local changes

- Frontmatter `name` changed from `neo4j-cypher-skill` to `neo4j-cypher` to match the directory.
- Added an "AURORA installation note" after the frontmatter. It turns `CLAUDE.md` Module 5 rules
  (`graph_schema.Label`/`Rel`, parameters only, `GraphSyncService` as the only writer, inferred edges
  never verified, manual non-destructive schema init) into rules that apply over the skill's advice.
- 2026-10-31: extended the note to cover conflicts found by `scripts/validate_skills.py`: Neo4j 5
  syntax instead of `CYPHER 25`, no APOC, and no `DELETE`/`LOAD CSV`/bulk-transaction writes.
- Content check (2026-10-31): `LICENSE.txt`, `README.md` and all 8 `references/*.md` are identical to
  upstream at the commit above (line endings normalized); `SKILL.md` differs only by the rename and note.

## Omitted

- `scripts/` (`define_schema.py`, `generate_schema.py`, `import_neo4j_schema.py`). They are executable
  Python, and schema init must stay manual in AURORA.
- The sibling skills in the same repository (driver, Aura, GDS, import, migration, CLI, and others).
  The Cypher skill points to `neo4j-migration-skill` and `neo4j-cli-tools-skill`, which are not installed.

## Notes

Neo4j 2025.x and 2026.x syntax. Verify against the Neo4j version AURORA actually runs before relying on
a syntax claim. Live Neo4j is still unverified in this project.
