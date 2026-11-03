---
name: website-seo-discovery
description: "Map a configured website's current state for SEO: niche, audience, offers and goals; sitemap and robots; key pages with titles, descriptions, canonicals, headings, schema, images and links; technical signals; and the read-only state of its source repository. Produces a dated website profile and discovery findings. Use as the discovery step of /seo-audit and /seo-content-optimization, or when asked to understand a site before SEO work. Do NOT use to judge content quality or rankings."
metadata:
  author: AURORA
  version: "1.0.0"
  category: seo-intelligence
---

# Website SEO discovery

## When to activate

Discovery step of `/seo-audit` and `/seo-content-optimization`; "understand this website",
"what pages do we have", or before any SEO work on a site without a recent profile.

## Required inputs

- Website config (via `seo-learning-and-memory` `context`): base URL, key pages, excluded paths,
  repository path, markets.
- The previous profile, if any, to report what changed.

## Tools and integrations

- `seo-data` MCP: `analyze_sitemap(site)`, `check_robots_txt(site, paths)`, `audit_page(url)`,
  `check_links(url)`. Free, no key. If the server is absent, use WebFetch for single pages and
  record the MCP as unavailable.
- Read-only file access to the configured repository (`repository.path`, `seo_paths`): read files
  and run read-only git (`git -C <path> status --short`, `branch -vv`, `log -n 20 --oneline`).
  Never write, commit, push or deploy there.
- Optional: AURORA Module 3 website mapping (`/api/v1/research/websites`) for a bounded crawl.

## Execution steps

1. **Profile basics** (once per site, refresh when the config changes): niche, audiences, offers,
   conversion goals, from the config and the homepage. Do not infer facts that are not on the site.
2. **Sitemap and robots**: `analyze_sitemap` (URL count, lastmod freshness, non-200 samples),
   `check_robots_txt` for the key pages and `/blog/` paths (Googlebot and a sample AI crawler).
3. **Key pages**: `audit_page` for every `key_pages` entry plus a rotating sample of 10 posts
   (prefer posts with open findings or recent changes). Collect: status, redirect chain, title and
   length, description and length, canonical, robots meta, H1 count, H2 outline, word count,
   images without alt, internal/external links, JSON-LD types, OG tags.
4. **Technical probes**: a guaranteed-missing URL (soft 404 check), `http://` and `www.` variants
   (redirect to the canonical host), one retired slug from the redirects file if configured.
5. **Links**: `check_links` on the homepage and two hubs (broken internal/external links).
6. **Repository state** (read-only): current branch, unpushed or undeployed commits that touch
   `seo_paths`, so findings can say "fixed in code, not live yet".
7. **Compare with the previous profile**: new or removed URLs, title/description changes, new
   schema types.
8. Save the profile with `profile <site> --file <profile.json>` (keys: `discovered_at`,
   `sitemap`, `robots`, `pages` [url, title, title_len, description_len, canonical, h1_count,
   word_count, schema_types, missing_alt], `technical_probes`, `repository_state`,
   `changes_since_last_profile`).

## Analysis methodology

Turn observations into findings only when they break a stated rule: the site's config rules first
(title/description limits, editorial rules), then the generic ones in the specialist skills
(`seo-toolkit-technical-audit`, `seo-toolkit-page-audit`). Discovery reports facts; severity is
decided in prioritisation.

## Output format

- The profile JSON (stored).
- Discovery findings (payload format in `seo-learning-and-memory`), with `checked.categories`
  listing what was probed, for example `technical/soft-404`, `on-page/metadata`,
  `technical/robots`, `technical/sitemap`, `links/broken`.
- Pages analysed and the tool calls made (with failures).

## Quality checks

- Each page fact comes from a fetch in this run, with the tool named.
- Live state and repository state are kept apart: "live shows X; repository branch Y already changes it".
- Sampled pages are listed so the next run can rotate the sample.

## Error handling

- Timeouts or 5xx: retry once later in the run, then record the URL as not fetched.
- Bot protection or blocked fetches: record the block; never try to evade it.
- Quota errors (PageSpeed): mark Core Web Vitals "not measured", add a blocker only if CWV was the
  focus of the run.

## Website-specific configuration

Key pages, excluded paths, repository paths and markets come from the website config. Excluded
paths (payment, auth, thank-you pages) are never fetched.

## How findings are stored

The profile replaces `sites/<site>/website-profile.json`; findings and tool usage go into the
execution through `record`.

## Improving this skill

Propose additions (for example a new technical probe that caught a real issue twice) through
`seo-learning-and-memory`; apply only after approval.
