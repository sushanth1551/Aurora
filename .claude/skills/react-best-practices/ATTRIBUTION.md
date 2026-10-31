# Attribution

- **Source repository:** https://github.com/vercel-labs/agent-skills
- **Original path:** `skills/react-best-practices` (SKILL.md, AGENTS.md, README.md, metadata.json, `rules/*.md`)
- **Commit:** `dc8367e6f91c022d83361f03c3313fa05e848ee5` (corrected 2026-10-31; see Notes)
- **Retrieved:** 2026-10-31
- **License:** MIT, declared in the skill's SKILL.md frontmatter and in the repository README. The repository
  has no separate LICENSE file, so no license copy is included here.
- **Status in AURORA:** adapted

## Local changes

- Frontmatter `name` changed from `vercel-react-best-practices` to `react-best-practices` to match the directory.
- No other text changed.

## Notes

The rules target React and Next.js performance (bundle, rendering, server actions). Several rules are
Next.js-specific (`server-*`, `async-api-routes`). AURORA's frontend is Vite, so ignore those rules
unless a Next.js route is added.

The hash first recorded here (`1a2c79d4…`) does not exist in the repository (GitHub API: HTTP 422).
On 2026-10-31 the installed files were compared with upstream at `dc8367e` (the latest commit touching
`skills/react-best-practices`, 2026-04-14): `AGENTS.md`, `README.md`, `metadata.json` and all 72
`rules/*.md` files are identical (line endings normalized; rules compared by git blob hash), and
`SKILL.md` differs only by the documented rename. The exact commit of the original download is unknown;
`dc8367e` is a commit whose content matches.

`AGENTS.md` links to three rule files that do not exist upstream either (`async-defer-await.md`,
`async-cheap-condition-before-await.md`, `server-hoist-static-io.md`); the rules themselves are in `rules/`.
