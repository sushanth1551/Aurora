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

## 8. Status (verified 2026-11-03)

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
