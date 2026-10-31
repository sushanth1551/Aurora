# Attribution

- **Source repository:** https://github.com/anthropics/skills
- **Original path:** `skills/webapp-testing` (SKILL.md and LICENSE.txt)
- **Commit:** `ca45fc496a3436251f191354091d557da31c15b5`
- **Retrieved:** 2026-10-31
- **License:** Apache-2.0 (LICENSE.txt copied from the skill folder)
- **Status in AURORA:** adapted (scripts omitted)

## Local changes

- Only `SKILL.md` and `LICENSE.txt` were copied.
- 2026-10-31: added an "AURORA installation note" after the frontmatter, stating that the helper
  scripts are absent and Playwright is not installed. The rest of the text is unchanged.
- Content check (2026-10-31): `LICENSE.txt` and the upstream part of `SKILL.md` are identical to upstream
  at the commit above (line endings normalized).

## Omitted

- `scripts/with_server.py` and `examples/*.py`. They are executable Python and are not run in AURORA.
- Playwright is not a dependency of this project. The skill cannot run until Playwright is installed,
  which needs an explicit decision because it adds a browser dependency.

## Notes

Use it only against the local development server, never a published site. Any browser check that
fetches a URL must still respect `UrlPolicy` when it touches research targets.
