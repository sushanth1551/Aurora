---
name: seo-site-crawl
description: "SEO Intelligence free, repeatable full-site crawl for a configured website: every rule of AURORA's technical audit (robots, sitemaps, status codes, redirects, indexability, titles, descriptions, H1, canonicals, hreflang, structured data, image alt, mixed content, internal links) on directly fetched HTML (no Firecrawl credits), plus soft-404 probe, images without dimensions, security headers and thin posts; in the same pass the YMYL accuracy checks, video embed checks and a read-only source map that points each finding to the file and line in the website repository. Use for /seo-site-crawl, 'crawl my site', 'technical SEO check', 'where in the code is this title'."
user-invocable: true
argument-hint: "[site-id] [--max-pages N]"
metadata:
  author: AURORA
  version: "1.0.0"
  category: seo-intelligence
---

# /seo-site-crawl

## When to activate

"Crawl the site", "technical SEO check", "what is broken on the site", "where is this title in
the code", or as the scripted technical step of `/seo-audit`.

## Required inputs

Website id; optional `repository.path` (read-only) for the source map.

## Tools and integrations

`.venv/Scripts/python scripts/seo_insights.py crawl <site> [--max-pages 150] [--no-ymyl]
[--no-video] [--no-source-map] [--execution <id>]`. Every request goes through `UrlPolicy`
(public hosts only, redirects re-validated); robots.txt is honoured for AURORA's own user agent;
`git ls-files` and text search only in the repository.

## Execution steps

1. Run the command; read the report and `insights/site-crawl-latest.json`.
2. Group results: technical (`tech:<check_id>`), site-wide extras, YMYL (`seo-ymyl-accuracy`),
   video (`seo-video`); give the file:line evidence when present.
3. For fixes in the website repository, hand over to `seo-content-optimization` (branch +
   approval). Never edit the repository from this skill.

## Analysis methodology

- Same input, same findings: no LLM in the checks. Pages that render content only in the
  browser show less text than users see; the crawl says what it fetched.
- Soft 404: one random non-existent URL must return 404.
- Blog-wide issues are one finding per check (`<check>:blog`) listing every post; key pages keep
  their own finding (`<check>:<path>`).
- A crawl that stopped early (budget or time) claims no rechecks, so nothing is wrongly resolved.

## Output format

Chat summary by severity and type, then the report path.

## Quality checks

Do not repeat findings another skill already holds under the same key; one issue, one key.

## Error handling

Site unreachable or policy-rejected: the run says so; nothing is inferred for unfetched pages.

## Website-specific configuration

`base_url`, `excluded_paths`, `rules` (title/description limits, minimum blog words), `ymyl`,
`repository.path`.

## How findings are stored

Ledger findings, `insights/site-crawl-<date>.json`, `insights/videos-videoobject-proposals.json`,
the `site-crawl` execution.

## Improving this skill

Through proposals (`seo-learning-and-memory`) and `/seo-update-skills`.
