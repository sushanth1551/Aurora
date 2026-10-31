# Attribution

- **Source repository:** https://github.com/markus41/claude
- **Original path:** `plugins/fastapi-backend/skills/fastapi-patterns` (SKILL.md only)
- **Commit:** `f971cbfa27f0d1cd9d885bb9ae0f325dbe90dc27`
- **Retrieved:** 2026-10-31
- **License:** MIT (LICENSE.txt copied from the repository root; copyright Golden Armada Team, 2025)
- **Status in AURORA:** adapted

## Local changes

- Frontmatter `name` changed from `FastAPI Patterns` to `fastapi-patterns` (lowercase, hyphenated).
- Added an "AURORA installation note" after the frontmatter: injection, no import-time connections,
  `AuroraException` errors, Pydantic schemas. The rest of the text is unchanged.

## Not copied

- Sibling skills in the same plugin (`beanie-odm`, `fastapi-k8s`, `keycloak-fastapi`, and others). They
  cover MongoDB, Kubernetes and identity, which AURORA does not use.

## Notes

The skill's examples include SQLAlchemy, JWT and Keycloak patterns. Follow `CLAUDE.md` where they
differ. The upstream repository is a general-purpose plugin collection, not a FastAPI reference.
