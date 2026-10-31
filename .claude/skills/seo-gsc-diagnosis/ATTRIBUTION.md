# Attribution

- **Source repository:** https://github.com/LukeRenton/google-search-console-mcp
- **Original path:** `skills/seo` (SKILL.md and `references/`)
- **Commit:** `cb25ef8cbceccd9020dc1e23a658d321d4725571`
- **Retrieved:** 2026-10-31
- **License:** MIT (LICENSE.txt copied from the repository root)
- **Status in AURORA:** adapted

## Local changes

- Frontmatter `name` renamed from `seo` to `seo-gsc-diagnosis` so it does not clash with the
  `seo` hub skill from claude-seo. The description is unchanged.
- Added this note: the `gsc` MCP server (`list_properties`, `query_search_analytics`,
  `inspect_url`, `list_sitemaps`, `submit_sitemap`) is **not installed** in AURORA. Without it the
  skill stops at preflight, as written. Installing the plugin and running OAuth are the user's
  decisions; the skill must not run `claude plugin install` or `npx ... auth` on its own.
- `submit_sitemap` and any Search Console write are external writes: they need the user's explicit
  approval in the session, per CLAUDE.md.
- AURORA does not implement Search Console access (Module 8 is planned). Do not present this skill as
  AURORA's Search Console integration.

## Files copied

- `SKILL.md`
- `references/00-overview.md`, `01-technical.md`, `02-content-onpage.md`, `03-ai-search-apis-links.md`
- `LICENSE.txt`

## Notes

The upstream `references/00-overview.md` says its chapters are third-party claims: about 60 `[VERIFY]`
bullets are not yet checked against primary sources. Treat the reference chapters as leads and verify
them before acting on them. The SKILL.md's "Reality checks" section is also dated 2026-09.
