# SEO Intelligence: reusable SEO agent, knowledge base and AURORA integration

Status 2026-11-03: phase A implemented and verified (see *Status*). This layer turns the SEO work
done for NextSteps into a repeatable, multi-website process: one command (`/seo-audit`) loads what
is already known, analyses the current site with the registered skills, compares with history,
stores everything and reports which skills and tools it used.

## 1. What existed before (evidence, not assumptions)

Reconstructed from Claude Code transcripts, the NextSteps repository and report files:

| Date | Where | What happened | Skills actually invoked |
| --- | --- | --- | --- |
| 2026-09-21 | NextSteps repo | Single-page pre-launch audit (`nextstepsusmle.com-audit/`) | unknown (predates transcripts) |
| 2026-11-01 | system32 session | `seo-data` MCP server written (`C:\Users\other\seo-mcp\server.py`) | none |
| 2026-11-01 | SEOWORK session | Competitor gap workbook (seo-data MCP, WebSearch) | `anthropic-skills:xlsx` |
| 2026-11-01 | Aurora session | Site audit (~77/100, judgement-based), GEO, comparison page, two drafts | `seo-audit` (claude-seo, now `seo-full-site-audit`), `seo-geo`, `seo-competitor-pages`, `seo-content-brief`, `seo-blog-writer` |
| 2026-11-02 | Aurora sessions | GSC India/US reports via AURORA's GSC API, SerpApi/Firecrawl (20 approved credits), 69-post crawl | `seo-toolkit-performance-analysis`, `seo-gsc-diagnosis`, `seo-toolkit-keyword-research`, `seo-competitor-gap-analysis` (blocked: no SE Ranking), `seo-toolkit-content-brief`, `seo-toolkit-prioritization` (method only) |
| 2026-11-02 | NextSteps session | 10 SEO commits on branch `update-blog-titles` (not pushed or deployed) | none (manual, rules from the owner) |

No gsc MCP, SE Ranking, DataForSEO or Firecrawl MCP call appears in any transcript. Work from
Aug-Sep 2026 predates the transcripts and cannot be attributed. The per-skill record is in
`seo-intelligence/skills/skill-registry.json` (`historical_use`).

## 2. Architecture

```
Claude Code                         AURORA backend                      Knowledge base (git)
/seo-audit ... /seo-update-skills   app/seo/intelligence/               seo-intelligence/
  └ skills (.claude/skills)           service.py   SeoAgentService      config/sites/<site>.json
  └ seo-intelligence-agent            history.py   HistoricalAudit      sites/<site>/findings.json
  └ seo-data MCP (free checks)        report.py    SeoReportGenerator   sites/<site>/executions/
  └ scripts/seo_intelligence.py ───►  knowledge.py SeoKnowledgeRepo ──► sites/<site>/reports/
                                      adapters.py  technical audit      sites/<site>/research/
                                    app/seo/skills/                     sites/<site>/metrics/
                                      registry.py  SkillRegistry  ◄──── skills/skill-registry.json
                                      improvement.py SkillImprovement ► skills/proposals, CHANGELOG
                                    api/routes/seo_intelligence.py  (/api/v1/seo/intelligence)
                                    reuses: seo/technical (audits), seo/keywords, monitoring (GSC)
```

The eight requested components:

| Component | Implementation |
| --- | --- |
| SEO Agent Service + Execution Orchestrator | `SeoAgentService` (`context`, `plan`, `start`, `record`, `finish`, decisions); the per-mode step list comes from the registry |
| Skill Registry | `SkillRegistry`: reviewed role/mode map joined with `SKILL.md` frontmatter, version and SHA-256 hash; availability per step (`missing_setting`, `runtime_check`, ...) |
| Website Configuration Manager | `WebsiteConfig` + `SeoKnowledgeRepository.load_config/save_config`; one JSON file per site |
| SEO Knowledge Repository | `SeoKnowledgeRepository` (atomic JSON writes, per-site folders, validated ids) |
| Historical Audit Service | `HistoricalAuditService.merge`: new / recurring / reopened / resolved / not rechecked |
| SEO Report Generator | `render_report`: deterministic Markdown with every section of the execution report |
| Skill Versioning and Improvement | content hashes per execution; `SkillImprovementService` proposals, approval, apply, changelog |
| External SEO providers | registry `requires` entries (settings and MCP servers); existing AURORA adapters (SerpApi, Firecrawl, GSC) |

Decision D7 of `final-product-plan.md` deferred a runtime skill registry. This registry is
deliberately smaller than the one in `module-6-design.md` §2.4: it reads metadata and hashes and
never injects skill text into a prompt or lets a skill add a tool, URL or write.

## 3. Comparison rules

A finding keeps one stable `key` (`<check>:<scope>`, `tech:<check_id>` for imported AURORA audits).
After each execution:

- observed and new: **new**; observed and already active: **recurring**; observed but previously
  resolved: **reopened**.
- not observed **and** its key or category is in the execution's `checked` scope: **resolved**
  (**verified** if it was `implemented`). Not observed and not rechecked: **not rechecked**, still open.
- observed while marked `implemented`: kept as implemented, flagged "check deployment".
- `rejected` and `deferred` are human decisions; later executions never override them.

## 4. Learning without silently changing rules

Executions store evidence and outcomes; they do not change methodology. A pattern becomes a
**proposal** (`propose`): it pins the skill's content hash, is flagged when it rests on a single
execution, and can target only AURORA-owned skills (third-party skills are reused unchanged). The
user approves it in `/seo-update-skills` (`decide --by <name>`), `apply` refuses stale proposals,
and each applied change is logged in `seo-intelligence/skills/CHANGELOG.md` and visible in git.

## 5. NextSteps and AURORA: repository strategy

```
C:\Users\other\Documents\Aurora   (AURORA, owned by you; the engine)
  .claude/skills, .claude/agents   generic SEO methodology + agent
  backend/app/seo/intelligence     services, API
  seo-intelligence/config/sites/   one config per connected website
  seo-intelligence/sites/<site>/   that website's findings and history only

C:\Users\other\nextsteps-usmle     (official NextSteps site; separate git repository)
  unchanged by AURORA; read-only from AURORA's side
```

- The repositories stay separate; nothing is copied or merged. AURORA reaches NextSteps through
  two interfaces only: the **public website** (read-only HTTP checks via `seo-data`/UrlPolicy) and
  the **repository path in the site config**, read-only (files, `git status/log/branch`).
- Approved changes travel as a **change list** in the execution report. Implementation happens in
  the NextSteps repository on a branch (`seo/<execution-id>`), only after the owner approves the
  list, through `/seo-content-optimization`; AURORA never pushes, merges or deploys. A later phase
  can replace this with a pull-request adapter (`publishing/`), still approval-gated.
- Why: NextSteps production stays under its owners' control, AURORA remains a self-contained
  portfolio project, the same engine serves any site (add a config), and site knowledge is
  isolated per folder.

Adding a website: copy `config/sites/nextsteps.json` to `config/sites/<id>.json`, edit it (no
secrets), run `python scripts/seo_intelligence.py validate`, then `/seo-audit <id>`.

## 6. Phases

| Phase | Scope | Status |
| --- | --- | --- |
| A | File-backed knowledge base, services, CLI, read-mostly API, audit import, skills, agent | done (this change) |
| B | Frontend page (websites, ledger, execution history, reports) reusing `services/api.ts` | planned |
| C | PostgreSQL repository behind the same interface; one migration after the branch migration chains are reconciled (`0006`-`0009` differ between `feature/final-product` and `feature/integration-aurora`) | planned |
| D | Run modes as AURORA background jobs (reuse `orchestration/` from `feature/integration-aurora`): technical audit + keyword run + GSC sync, then import | planned |
| E | Approval-gated pull-request adapter to connected site repositories | planned |

The knowledge base is JSON in git in phase A, which gives diffs and history for free. It holds
structured findings and short evidence only: raw HTML, provider payloads and exports belong in
`data/` (git-ignored) or the session scratchpad.

## 7. Usage

```powershell
# one command in Claude Code (AURORA workspace)
/seo-audit nextsteps
/seo-competitor-analysis nextsteps
/seo-content-optimization nextsteps from-findings
/seo-performance-review nextsteps
/seo-update-skills
/seo-monitor nextsteps        # also runs every 14 days via Windows Task Scheduler

# bookkeeping CLI (used by the skills; safe to run by hand)
.venv\Scripts\python scripts\seo_intelligence.py context nextsteps
.venv\Scripts\python scripts\seo_intelligence.py history nextsteps
.venv\Scripts\python scripts\seo_intelligence.py report nextsteps
.venv\Scripts\python scripts\seo_intelligence.py validate

# API (token required)
GET  /api/v1/seo/intelligence/websites
GET  /api/v1/seo/intelligence/websites/{site}/context | plan?mode= | findings | executions
GET  /api/v1/seo/intelligence/websites/{site}/executions/{id} | .../report
POST /api/v1/seo/intelligence/websites/{site}/imports/technical-audits/{audit_id}  {"confirm": true}
GET  /api/v1/seo/intelligence/skills | skills/proposals
```

## 8. Scheduled monitoring and learning from your changes (`/seo-monitor`)

Every 14 days the Windows task **"AURORA SEO Monitor (nextsteps)"** runs
`scripts/seo-monitor.ps1 -Site nextsteps -StartDatabase` (registered by
`scripts/register-seo-monitor.ps1`; remove with `-Unregister`). It starts PostgreSQL (needed for
the sealed Search Console token), runs `scripts/seo_monitor.py`, logs to
`data/logs/seo-monitor-*.log` and shows a Windows notification. No paid API is called; nothing is
written to the website, Search Console or git.

What one run does (`backend/app/seo/intelligence/monitor.py`):

1. **Your changes:** read-only `git log` of the site repository (all local branches and
   `origin/main`), keeping commits that touch the configured SEO paths. Post JSON files are
   diffed per slug: title, description (excerpt), FAQ, body word count, new posts
   (`changes.py`). Stored in `sites/<site>/changes/commits.json`.
2. **Go-live:** a commit counts as live when it is on `origin/<production branch>` (as last
   fetched) or the live page shows its new title. Each commit records how its go-live date was
   established.
3. **Checklist** (`sites/<site>/checklist.json`): per changed page: deploy, live title, request
   indexing (manual; detected from the site's reindex tracker), ranking checks 14/28/56 days
   after go-live, and re-verifying linked findings. Older live-title/reindex items for the same
   page are superseded; changes older than 30 days at the first sync skip manual items.
4. **Rankings:** Search Console `date x page` rows, last 14 days vs the previous 14
   (final data, 3-day lag). Drops of 3+ positions on pages with 100+ impressions become
   `ranking-drop:<path>` findings, which later runs mark resolved or recurring.
5. **Learning:** each ranking check compares the page with **all blog pages over the same
   windows** (`rankings.relative_verdict`), so site-wide growth or a Google update is not
   credited to a change. Results are aggregated by change type in
   `sites/<site>/learning/change-effects.json`. When one change type beats (or trails) the site
   trend in >= 70% of >= 5 pages at 28 days, it is reported as a **candidate rule**; it becomes a
   website rule or a skill proposal only after you approve it (`/seo-monitor`, `/seo-update-skills`).
6. **Skill edits:** skills changed outside the proposal process (for example edited by you) are
   detected by content hash, logged in `skills/CHANGELOG.md`, and used from then on
   (`skills/skill-hashes.json` is the baseline).

First result (2026-11-03): 67 SEO commits from the last 90 days tracked; 10 not on production;
measured against the site trend, no change type reliably beats it yet (for example title changes
at 56 days: 8 pages ahead, 12 behind), so no rule is proposed.

Limits: go-live dates of changes already on production at the first sync use the commit date;
`origin/main` is only as fresh as the last `git fetch`; the live-title check proves deployment
for retitled pages only; positions are page averages across all queries.

## 9. Status (verified 2026-11-03)

- 16 tests in `backend/tests/test_seo_intelligence.py`: models, path safety, comparison rules,
  lifecycle, report sections, registry availability, proposal approval/stale handling, audit
  import, API, CLI, and validity of the committed registry and knowledge base.
- Executions recorded for nextsteps: `20261103-084156-baseline-import` (35 findings imported from
  evidence, 38 implemented changes with commit references, 18 sourced GSC metrics) and
  `20261103-084638-audit` (first live run, free `seo-data` checks: 1 new, 13 recurring, 0 resolved,
  status partial because PageSpeed's quota was exhausted).
- Not verified: the API import against a live PostgreSQL audit (tested with a fake repository);
  the GSC, SerpApi and Firecrawl paths inside the new modes (they reuse the existing, separately
  tested AURORA services).

## 10. Insights: scripted analyses (added 2026-11-04)

Deterministic analyses in `backend/app/seo/intelligence/insights/`, run by
`scripts/seo_insights.py <command> <site>` (each one is an execution, or records into
`--execution <id>`), with one skill each and their own registry modes. No LLM is involved in any
check; inputs are injected, so every module is unit-tested offline
(`backend/tests/test_seo_insights.py`).

| Feature | Command / mode | Data | What is new compared with common SEO agents |
| --- | --- | --- | --- |
| Quick wins (striking distance 4-20) | `opportunities` / `seo-opportunities` | Search Console (free) | "Extra clicks" use the site's **own** CTR curve, never a generic one; too little data = not estimated |
| Low CTR at good positions | same | same | Judged against the site's own CTR at that position (snippet problem vs ranking problem) |
| Content decay, prune review, merges, never-shown pages | same | same | Decay vs the previous **and** the page's best 28-day window, with the queries that lost the clicks; merges only from real query overlap; pages never shown get `no-impressions` (index check), not "delete" |
| Lead value per page | `opportunities --conversions file.csv` | GA4 / CRM export | Opportunities ranked by extra leads (page's own conversions per click) |
| Alerts | `alerts` / `seo-alerts`, weekly task | Search Console + URL Inspection (free) | Robust z-score (median/MAD) over 8 weeks, so one odd week neither hides nor fires an alert; index status of key pages |
| Rank tracking per market | `ranks` / `seo-rank-tracking` | Search Console `query x country` (free) | Free daily positions per market (IN, US) with history; SerpApi only as budgeted weekly spot checks |
| SERP features | `ranks --serp` | SerpApi (paid, monthly budget) | Featured-snippet owner, AI Overview presence and citations, People Also Ask |
| Keyword volume, difficulty, backlinks | `keyword-data [--backlinks]` | GSC demand (free) + DataForSEO (paid, USD budget) | Budget enforced against the cost DataForSEO reports; never estimated |
| AI-search visibility | `ai-visibility`, `record-ai-answer` / `seo-ai-visibility` | robots.txt, llms.txt, SERP checks, recorded answers | AI crawlers judged **by purpose** (answer/search vs training) with RFC 9309 matching; prompt panel share of voice from observed answers only |
| Free repeatable crawl | `crawl` / `seo-site-crawl` | direct HTML via `UrlPolicy` | Every technical-audit rule without Firecrawl credits, plus soft 404, image dimensions, security headers, thin posts |
| Source-code locations | part of `crawl` | website repository (read-only) | Each finding gets `file:line` of the title/description/slug in the source |
| YMYL accuracy | part of `crawl` / `seo-ymyl-accuracy` | crawled HTML + facts registry | Unsourced fees/rates, stale years, claims (negations and teasers skipped), owner-verified facts with review dates |
| Video SEO | `videos`, `crawl` / `seo-video` | yt-dlp metadata (optional `video` extra) | VideoObject proposals from real upload dates/durations; testimonial videos matched to posts |
| Competitor change watch | `competitors` / `seo-competitor-watch` | competitors' sitemaps (free) | New pages on your keywords and topics you do not cover; bulk lastmod changes recognised |
| Exports and dashboard | `export`, `dashboard`; API `GET /websites/{id}/insights[/{kind}]`; dashboard tab **SEO Agent** | knowledge base | CSV (Excel-ready) and one dashboard document |
| Parallel execution | registry step `group` | - | Steps sharing a group run as parallel subagents; payloads are recorded one at a time |
| Rollback | `rollback-plan`, `rollback-prepare --approved-by` | website repository | Stages a revert on `seo-rollback/<sha>`; never commits, pushes or touches the production branch |
| Portable plugin | `scripts/build_seo_plugin.py` | - | Builds `data/plugins/seo-intelligence/` (plugin.json + marketplace.json, agents, 38 skills) |

**Keys and rechecks.** Findings follow `<check>:<scope>` and reuse existing keys (`soft-404:site`,
`security-headers-missing:site`, `low-ctr:<path>`, `unsupported-claim:<path>`, ...); per-post
issues are one `<check>:blog` finding. A run claims a recheck only for keys its test could observe
(for example `low-ctr:` only for pages with top-5 rows), so nothing is marked resolved or
verified by a different test.

**Storage.** Snapshots, rank history, budget ledgers, AI answers and the video catalog:
`seo-intelligence/sites/<site>/insights/`. Competitor sitemap URL lists and CSV exports:
`data/seo-intelligence/<site>/` (never committed).

**Budgets.** `rank_tracking.serpapi_monthly_budget` (searches) and
`keyword_data.monthly_budget_usd` default to 0; only the owner raises them.

**Verified 2026-11-04 (nextsteps, free runs):** crawl 83 pages (28 findings, 5 matched existing
ledger keys), opportunities (10 striking-distance pages, 11 decaying, 1 dormant), alerts (7),
rank tracking (10 of 20 keyword-market pairs with impressions), keyword demand (49 keywords, no
paid provider), AI visibility (no AI crawler blocked, llms.txt valid), competitor watch (3
competitors), video catalog (35 videos). Not verified live: SerpApi checks and DataForSEO (no
budget set), the weekly alerts task (registration needs the owner to run
`scripts\register-seo-alerts.ps1`), the dashboard tab against the running API (it needs an
AURORA restart to load the new routes).
