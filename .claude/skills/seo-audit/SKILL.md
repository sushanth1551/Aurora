---
name: seo-audit
description: "One-command SEO Intelligence execution for a configured website (default: nextsteps): loads previous knowledge, discovers the current site, runs the registered technical, on-page, content, linking and keyword skills, compares with past audits, prioritises Critical/High/Medium/Low, stores findings and execution history, and writes a transparent report of skills and tools used. Use when the user runs /seo-audit or asks for a full or repeat SEO audit of a configured site. For one URL use seo-toolkit-page-audit; for the plain claude-seo sweep use seo-full-site-audit."
user-invocable: true
argument-hint: "[site-id] [focus notes]"
metadata:
  author: AURORA
  version: "1.0.0"
  category: seo-intelligence
---

# /seo-audit: SEO Intelligence orchestrator

You are the SEO Intelligence agent (see `.claude/agents/seo-intelligence-agent.md`). This skill
runs the `audit` mode end to end. The step list comes from the skill registry, not from memory:
`seo-intelligence/skills/skill-registry.json`.

## When to activate

`/seo-audit`, `/seo-audit <site-id>`, or "audit the site again", "what changed since the last
audit", "run the full SEO workflow". If the request names a single URL, use
`seo-toolkit-page-audit` instead; for competitors, content edits or Search Console review use
the other `/seo-*` modes.

## Required inputs

- Website id: the first argument, else the only configured site, else ask. `nextsteps` is the
  initial site.
- Optional: focus notes, new report files, an approved credit budget for paid APIs.

## Tools and integrations

| Need | Preferred | Fallback | If neither |
| --- | --- | --- | --- |
| Pages, metadata, headings, links, schema | `seo-data` MCP `audit_page`, `check_links` | WebFetch of the page | record blocker |
| Sitemap, robots | `seo-data` `analyze_sitemap`, `check_robots_txt` | WebFetch | record blocker |
| Core Web Vitals | `seo-data` `pagespeed` | none | "not measured" |
| Full crawl with rules | AURORA technical audit (`/api/v1/seo/audits`, Firecrawl) then import | seo-data sampling | partial |
| Search Console | AURORA GSC (`/api/v1/gsc`) | supplied exports | skip GSC-based steps |
| SERPs | AURORA SerpApi (budgeted) | Claude WebSearch (say so) | skip |
| Repository state | read-only file reads in the configured repo path | none | note it |
| Bookkeeping | `scripts/seo_intelligence.py` | none | stop: cannot store |

Paid calls (SerpApi, Firecrawl) need an explicit credit budget from the user before the first
call; no retries. Never run anything that writes to the website, Search Console or a CMS.

## Execution steps

1. **Load knowledge** (`seo-learning-and-memory`): `sites`, `context <site>`, `plan <site> --mode audit`,
   `start <site> --mode audit`. Tell the user the plan in one short table: step, skill, availability.
2. **Discover** (`website-seo-discovery`): current sitemap, robots, key pages, a sample of posts,
   and the repository's current branch and unpushed commits. Save the profile with `profile`.
3. **Reports** (`seo-report-analysis`, optional): only reports not already listed in a previous
   execution's `reports_processed`.
4. **Technical** (`seo-toolkit-technical-audit`): status codes, soft 404s, redirects, canonicals,
   noindex, sitemap/robots agreement, security headers, rendering. If an AURORA technical audit of
   this host finished since the last execution, import it
   (`POST /api/v1/seo/intelligence/websites/<site>/imports/technical-audits/<audit_id>`) instead of
   repeating the crawl.
5. **Full-site sweep** (`seo-full-site-audit`, optional): only on request; drop its numeric scores.
6. **Key pages** (`seo-toolkit-page-audit`, `seo-toolkit-schema`): every page in `key_pages` plus
   pages with open findings.
7. **Internal links** (`seo-toolkit-internal-linking`): orphans and weak inbound links.
8. **Content quality** (`seo-quality-audit`): thin pages, stale facts and years, unsupported
   claims, the site's editorial rules from the config.
9. **Cannibalisation and keyword fit** (`seo-toolkit-cannibalization`, `seo-keyword-research`):
   only with Search Console data or stored keyword research; otherwise mark the step skipped.
10. **AI search** (`seo-geo`, optional): crawler access and passage citability.
11. **Prioritise** (`seo-toolkit-prioritization`): Critical/High/Medium/Low with the rubric visible.
12. **Re-verify** open findings from `context` that this run can check, and every finding marked
    `implemented`: observed again means "not deployed or not effective".
13. **Store and report** (`seo-learning-and-memory`): `record` the payload (findings, skills,
    tools, pages, reports, checked scope, changes, learning), then `finish` with an honest status.
14. **Answer the user** with the report summary (below) and the report path. List proposed website
    changes as proposals awaiting approval; do not make them.

## Analysis methodology

- Every finding fills all fields: issue, evidence, affected URL, SEO impact, priority,
  recommended action, implementation status, expected outcome, verification method.
- Priority: Critical = blocks crawling/indexing/conversion or creates trust/legal risk now;
  High = clear, evidenced loss on important pages; Medium = real but limited; Low = hygiene or
  opportunity. Ties go to lower effort and higher reversibility.
- Compare, don't restate: lead with new, reopened and implemented-but-still-observed findings.
- Respect decisions: rejected recommendations stay rejected unless new evidence contradicts the
  recorded reason (then say so and ask).

## Output format (reply to the user)

1. Execution summary: site, date, status (completed/partial/blocked), pages and reports analysed.
2. New / resolved / recurring / reopened counts and the most important of each.
3. Top recommendations by priority (issue, evidence, action, verification).
4. Skills used and why; tools used, separating successful from unavailable or failed.
5. Proposed website changes awaiting approval; skill proposals awaiting `/seo-update-skills`.
6. Path of the full report in `seo-intelligence/sites/<site>/reports/`.

## Quality checks

- Status is `completed` only with no blockers; otherwise `partial` or `blocked`.
- Nothing is called fixed without re-observation after deployment.
- No numeric SEO scores, rankings promises, traffic forecasts, or volumes without a provider.
- Every claimed tool call happened in this session; unavailable tools are listed as unavailable.

## Error handling

- A step's tool is missing: mark the step skipped or the tool unavailable, add a blocker if the
  step was not optional, continue with the rest.
- The bookkeeping CLI fails: show the error, fix the payload; never hand-edit knowledge files.
- The user stops the run: `finish` as `partial` with a note so the next run starts cleanly.

## Website-specific configuration

All site facts (domain, key pages, markets, competitors, editorial rules, repository path,
approval list) come from `seo-intelligence/config/sites/<site>.json` via `context`. Do not
hardcode them here.

## How findings are stored

Through `seo-learning-and-memory`: execution record, finding ledger, implemented changes,
metrics, research snapshots, and the Markdown report (`docs/seo-intelligence.md`).

## Improving this skill

Do not edit this file during a run. Record patterns in `learning`, file a proposal with
`seo-learning-and-memory`, and apply it through `/seo-update-skills` after approval.
