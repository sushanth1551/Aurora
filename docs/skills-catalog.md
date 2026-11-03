# Skills catalog

Claude Code skills available to this project live in `.claude/skills/<name>/SKILL.md`. They are
**development guidance and reusable agent instructions**, not executable code: nothing in them
changes Module 1-4 behaviour, provider integrations, `.env`, the database or the API. All skill
text is treated as guidance that never overrides `CLAUDE.md`.

Last updated 2026-11-03 (section J added and `seo-audit` renamed to `seo-full-site-audit`; earlier: section I added 2026-11-01; sections G and H added 2026-10-31; validation script, module/workflow mapping and
validation results added). **109 skills** are installed. Commit hashes of every copied skill are
recorded in its `ATTRIBUTION.md`; run `python scripts/validate_skills.py` to check all of them (see
*Validation* at the end).

## Layout and discovery

- The directory is **flat** on purpose: Claude Code discovers skills at
  `.claude/skills/<name>/SKILL.md`, so grouping folders are not used. Grouping is by name prefix
  and by this catalog instead.
- Three sets coexist:
  1. **AURORA's own planning skills** (11): `web-research`, `serp-analysis`, `fact-verification`,
     `firecrawl-extraction`, `graphrag-research`, `knowledge-graph`, `seo-keyword-research`,
     `seo-content-strategy`, `seo-blog-writer`, `seo-quality-audit`, `wordpress-publishing`.
     Short, AURORA-specific, planning-only; untouched by this collection.
  2. **`claude-seo` plugin skills** (26, `seo` and `seo-*`, MIT, from
     https://github.com/AgriciDaniel/claude-seo): installed earlier as text only; their
     script-backed steps need the real plugin.
  3. **Skills collected from other repositories** (64): 25 in sections A-F (23 copied/adapted + 2
     locally authored), `seo-gsc-diagnosis` (G), 6 development skills (H) and 32 SE Ranking
     skills (I). Each has
     `ATTRIBUTION.md`; a license copy where the source ships one.
  4. **AURORA SEO Intelligence skills** (8, added 2026-11-03): the `/seo-*` execution modes and
     their support skills, authored here (section J; `docs/seo-intelligence.md`).
- Pick the **narrowest** skill that matches the task; use the orchestrator skills (`seo`,
  `seo-toolkit-orchestrator`) only for multi-domain requests. When two sets overlap, see
  *Duplicates* below.

## Collected skills

Status legend: **unchanged** = copied as-is; **adapted** = content preserved, with a prepended
"AURORA installation note", a rename and/or omitted scripts (listed in `ATTRIBUTION.md`);
**local** = authored here from official documentation, clearly labelled as such.

### A. Web research and crawling — Firecrawl

Source: https://github.com/firecrawl/firecrawl, path `skills/<name>` (mirrored upstream to
`firecrawl/skills`). License: ISC declared in each SKILL.md frontmatter; the repository is
AGPL-3.0 (copy included). Dependencies: none installed (Firecrawl is already integrated through
`backend/app/tools/firecrawl_tool.py`). AURORA modules: 2, 3 (existing adapter), 5 (ingestion).

| Skill | What it covers | How Claude should use it | Status |
| --- | --- | --- | --- |
| `firecrawl-build` | Hub: endpoint selection (`/scrape`, `/search`, `/interact`), integration patterns, verification | Choose the endpoint and request options; never install the SDK, run `npx firecrawl-cli` or write `.env`; AURORA's adapter is the only client | adapted |
| `firecrawl-build-scrape` | Single-URL extraction, formats, `onlyMainContent`, `maxAge` freshness and `cacheState` | When tuning page extraction or freshness rules in `FirecrawlTool.scrape_url`/`batch_scrape` | adapted |
| `firecrawl-build-search` | Query-first discovery, search vs. extraction separation, research/developer index caveats | When designing discovery flows; note AURORA uses SerpApi for discovery today | adapted |
| `firecrawl-build-interact` | Browser actions after scrape (forms, pagination, login-gated pages) | Only if a future module needs interaction; keep it behind `UrlPolicy` and human approval | adapted |

### B. Agent skill development — Anthropic

Source: https://github.com/anthropics/skills. License: Apache-2.0 (per-skill `LICENSE.txt` for
`skill-creator`; repository README for `doc-coauthoring`, which ships no license file).
Dependencies: none (Python scripts omitted). AURORA modules: all (development process).

| Skill | What it covers | How Claude should use it | Status |
| --- | --- | --- | --- |
| `skill-creator` | SKILL.md anatomy and frontmatter rules, skill design, test prompts, evals, description tuning; includes `references/official-skill-template.md` | When adding or improving a skill in `.claude/skills/`; scripts are omitted, so run packaging/evals manually or use the plugin-provided `anthropic-skills:skill-creator` | adapted |
| `doc-coauthoring` | Three-stage documentation workflow (context, refinement, reader testing) | When drafting specs, ADRs or module docs in `docs/` | unchanged |

### C. Agent orchestration — LangGraph (local reference)

Source: no skills exist in https://github.com/langchain-ai/langgraph; the skill was authored
locally from the official docs (URLs inside). License: LangGraph is MIT. Dependencies:
`langgraph` 0.6.11 is already installed as a declared dependency; nothing was added.
AURORA modules: 5-8 (workflows), approval gates in 7.

| Skill | What it covers | How Claude should use it | Status |
| --- | --- | --- | --- |
| `langgraph-orchestration` | TypedDict state and reducers, StateGraph/edges, `Command`/`Send` routing, checkpointers and `thread_id`, `interrupt()` approval, `RetryPolicy`, resume; AURORA rules and a step-to-service mapping | When implementing `backend/app/workflows/*.py`; verify 1.x-only APIs against the installed version first | local |

### D. Search engine research — SerpApi (local reference + official docs)

Source: https://github.com/serpapi/serpapi-python (no skills; `docs/` copied as references),
plus https://serpapi.com/search-api. License: MIT. Dependencies: none (the `serpapi` client is
**not** installed; AURORA's HTTPX adapter stays). AURORA modules: 2 (existing), 6 (keyword and
SERP intelligence).

| Skill | What it covers | How Claude should use it | Status |
| --- | --- | --- | --- |
| `serpapi-search-research` | Organic results, SERP sections, related searches/questions, localization, pagination, `json_restrictor`, `no_cache`/`zero_trace`, error mapping, cost bounds; states what AURORA's parser does and does not expose | When designing search-based research or SERP/keyword work; do not duplicate API calls | local (SKILL.md) + unchanged references |

### E. SEO intelligence — SEO Agent Toolkit

Source: https://github.com/datacas/seo-agent-toolkit, path `skills/<upstream name>`. License:
MIT. Dependencies: none (the Node `scripts/*.mjs` evidence tools were omitted; where a step
says `node scripts/...`, treat that evidence as `not_available`). Installed with the
`seo-toolkit-` prefix to avoid clashes; the shared evidence/security policy is in
`seo-toolkit-orchestrator/references/shared-policy/`. AURORA modules: 6 (intelligence),
7 (content), 8 (monitoring).

| Skill (upstream name) | What it covers | AURORA module | Status |
| --- | --- | --- | --- |
| `seo-toolkit-orchestrator` (`seo-orchestrator`) | Routing and synthesis across SEO skills; evidence-state preservation | 6 | adapted |
| `seo-toolkit-strategy` (`seo-strategy`) | Goals, positioning, topics, phased roadmap | 6 | adapted |
| `seo-toolkit-prioritization` (`seo-prioritization`) | Impact/confidence/effort/risk ranking with a visible rubric | 6, 8 | adapted |
| `seo-toolkit-keyword-research` (`seo-keyword-research`) | Intent classes, clustering test, page targeting, no unsourced volumes | 6 | adapted |
| `seo-toolkit-serp-analysis` (`seo-serp-analysis`) | Observed SERP composition, AI answer surfaces, cited sources | 6 | adapted |
| `seo-toolkit-content-brief` (`seo-content-brief`) | Specification for content that does not exist yet | 7 | adapted |
| `seo-toolkit-content-optimizer` (`seo-content-optimizer`) | Concrete edits to existing content, citability, passage structure | 7 | adapted |
| `seo-toolkit-page-audit` (`seo-page-audit`) | Single-page diagnosis across intent, metadata, content, links, schema | 6 | adapted |
| `seo-toolkit-technical-audit` (`seo-technical-audit`) | Site-wide crawlability, indexability, redirects, hreflang, rendering, CWV delivery | 6 | adapted |
| `seo-toolkit-schema` (`seo-schema`) | Truthful JSON-LD detection, validation and generation | 6, 7 | adapted |
| `seo-toolkit-internal-linking` (`seo-internal-linking`) | Link architecture, hubs, depth, orphans, anchors | 6 | adapted |
| `seo-toolkit-cannibalization` (`seo-cannibalization`) | Competing URLs for one intent; consolidation decisions | 6 | adapted |
| `seo-toolkit-programmatic` (`seo-programmatic`) | Scaled/templated page sets, thin-page and index-bloat controls | 6, 7 | adapted |
| `seo-toolkit-indexation-analysis` (`seo-indexation-analysis`) | Indexation state from crawl/sitemap/GSC snapshots | 8 | adapted |
| `seo-toolkit-performance-analysis` (`seo-performance-analysis`) | Clicks/impressions/CTR/position change analysis from supplied data | 8 | adapted |
| `seo-toolkit-ai-visibility` (`seo-ai-visibility`) | Crawler access evidence and observed AI mentions/citations | 6, 8 | adapted |

### F. Advanced SEO — SEO Mastery Agent Skills

Source: https://github.com/kpab/seo-mastery-agent-skills, path `.claude/skills/seo-mastery`
(SKILL.md + 8 reference files, each with a `last_verified` date). License: MIT. Dependencies:
none; `audit-workflow.md` contains shell snippets (curl loops, Lighthouse via npm) that are
documentation, not steps to run. AURORA modules: 6, 7.

| Skill | What it covers | How Claude should use it | Status |
| --- | --- | --- | --- |
| `seo-mastery` | Technical SEO, content/E-E-A-T, structured-data templates, Core Web Vitals, GEO/LLMO/AEO with evidence levels, Astro and Cloudflare edge SEO, 6-phase audit workflow | Reference depth for the topics above; load the topic reference file named in SKILL.md; the Japanese twin was not installed | unchanged |

### G. Search Console diagnosis — google-search-console-mcp

Source: https://github.com/LukeRenton/google-search-console-mcp, path `skills/seo` (MIT). Imported as
`seo-gsc-diagnosis` to avoid a clash with the `seo` hub. Needs the `gsc` MCP server, which AURORA does
not install; without it the skill stops at preflight. Its `references/` chapters are unverified
third-party leads (see `ATTRIBUTION.md`). AURORA module: 8 (monitoring, planned).

| Skill | What it covers | How Claude should use it | Status |
| --- | --- | --- | --- |
| `seo-gsc-diagnosis` | Differential diagnosis of indexing, impressions, CTR and post-deploy drops from Search Console data | Only with real GSC data supplied or the gsc server enabled; submitting sitemaps needs approval | adapted |

### H. Backend, graph, frontend and browser testing (added 2026-10-31)

| Skill | Source (commit) | License | AURORA module | When to use | Status |
| --- | --- | --- | --- | --- | --- |
| `fastapi-patterns` | markus41/claude `plugins/fastapi-backend` (`f971cbf`) | MIT | 1-6 (API) | Adding routers, dependencies or error handling to `backend/app/api/`; AURORA rules win | adapted |
| `neo4j-cypher` | neo4j-contrib/neo4j-skills `neo4j-cypher-skill` (`39247b9`) | MIT | 5 (graph) | Reviewing or writing read-only Cypher or the `MERGE` text inside `GraphSyncService`; labels/rels from `graph_schema` only; Neo4j 5 syntax, no APOC, no deletes/bulk writes (AURORA note) | adapted |
| `react-best-practices` | vercel-labs/agent-skills (`dc8367e`, corrected) | MIT (README; no license file upstream) | frontend | Reviewing React performance (rendering, state, bundle) in `frontend/src/`; ignore Next.js-only rules | adapted |
| `web-design-guidelines` | vercel-labs/agent-skills (`dc8367e`, corrected) | MIT (README; no license file upstream) | frontend | Auditing UI accessibility before merging frontend changes. **Fetches its rules from GitHub at use time** (unpinned; treat as untrusted reference) | unchanged |
| `frontend-design` | anthropics/skills (`ca45fc4`) | Apache-2.0 | frontend | Designing new UI screens or reshaping existing ones so they do not look templated | unchanged |
| `webapp-testing` | anthropics/skills (`ca45fc4`) | Apache-2.0 | frontend QA | Local UI checks with Playwright. Helper scripts omitted (AURORA note says so); **Playwright not installed** (Python or npm) | adapted |

Review of section H (2026-10-31): every file was compared with upstream at the recorded commit
(line endings normalized). `frontend-design`, `webapp-testing` (upstream part), all `neo4j-cypher`
references, README and license, the `fastapi-patterns` license, and all 77 `react-best-practices`
files match; the only differences are the documented renames and AURORA notes. The Vercel commit
originally recorded (`1a2c79d4…`) does not exist (GitHub HTTP 422) and was replaced by `dc8367e`,
whose content matches. `fastapi-patterns`' repository `markus41/claude` now redirects to a renamed
repository; the commit still resolves.

Not taken: `learn-skills.dev` (FastAPI best-practices, cloned empty: no SKILL.md found) and
`neversight`-style collections, which were not reviewed. Per-skill `ATTRIBUTION.md` files record the exact source paths.

### I. SE Ranking SEO skills (added 2026-11-01)

Source: https://github.com/seranking/seo-skills, path `skills/*`, commit
`fd6d1408f2e6a06454d81c07c29e0f04342eb9ba`. License: MIT. All **32** skills in the repository
were installed (the repository README's older changelog mentions 24-26; the current tree has 32).
13 names clash with installed skills and were prefixed `seranking-` (see the *Renamed* entries);
`ATTRIBUTION.md` and `LICENSE.txt` sit in every folder. Not installed: the repository's
`scripts/`, `extensions/` and `mcp.json` (downloaded executables and MCP configuration).
These skills expect the SE Ranking MCP/API (and, for six of them, Planable); neither is
configured in AURORA, so they are **reference text only**. Skill text that calls other skills by
their original name (for example `seo-page`) may refer to either set. 11 skills link to
repository `examples/`, which were not copied (validator warning `link_escapes`).
AURORA modules: 6 (reference only).

| Skill | What it covers | Notes | Status |
| --- | --- | --- | --- |
| `ai-search-gaps-to-social-campaign` | AI-search visibility gaps turned into a Planable social campaign | needs SE Ranking MCP/API and Planable; text-only here | unchanged |
| `client-onboarding-proposal` | Agency onboarding proposal from an SE Ranking audit plus Planable data | needs SE Ranking MCP/API and Planable; text-only here | unchanged |
| `local-gmb-visibility` | City-level rank tracking plus local/GBP social content | needs SE Ranking MCP/API and Planable; text-only here | unchanged |
| `seo-ads` | Paid-search competitive landscape (PPC keywords, ad copy, bidders) | needs SE Ranking MCP/API; text-only here | unchanged |
| `seo-agency-landing-page` | Demand-gen landing page for an SEO agency | needs SE Ranking MCP/API; text-only here | unchanged |
| `seo-ai-search-share-of-voice` | AI-search share of voice versus competitors | needs SE Ranking MCP/API; text-only here | unchanged |
| `seo-ai-social-report` | Combined SEO, AI-search and social report (SE Ranking + Planable) | needs SE Ranking MCP/API and Planable; text-only here | unchanged |
| `seo-api` | SE Ranking Data/Project API integration recipes (live tool schemas via MCP) | needs SE Ranking MCP/API; text-only here | unchanged |
| `seo-backlink-gap` | Referring domains linking to competitors but not the target | needs SE Ranking MCP/API; text-only here | unchanged |
| `seo-backlinks-profile` | Backlink profile health and disavow candidates (review only) | needs SE Ranking MCP/API; text-only here | unchanged |
| `seo-competitor-gap-analysis` | Keyword gap versus top organic competitors | needs SE Ranking MCP/API; text-only here | unchanged |
| `seranking-seo-competitor-pages` | X-vs-Y and alternatives page generation | Renamed from `seo-competitor-pages`; needs SE Ranking MCP/API; text-only here | adapted |
| `seo-content-audit` | E-E-A-T (60-item) + CITE (30-item) audit with publish verdict | needs SE Ranking MCP/API; text-only here | unchanged |
| `seranking-seo-content-brief` | Writer-ready content brief from SE Ranking data | Renamed from `seo-content-brief`; needs SE Ranking MCP/API; text-only here | adapted |
| `seranking-seo-drift` | Baseline and regression comparison | Renamed from `seo-drift`; needs SE Ranking MCP/API; text-only here | adapted |
| `seo-firecrawl` | Ad-hoc scrape/map/crawl through Firecrawl MCP (raw HTML, JSON-LD) | needs SE Ranking MCP/API; text-only here | unchanged |
| `seo-gaps-to-social-campaign` | Keyword gaps turned into a Planable social campaign | needs SE Ranking MCP/API and Planable; text-only here | unchanged |
| `seranking-seo-geo` | URL-level AI Overview citation analysis | Renamed from `seo-geo`; needs SE Ranking MCP/API; text-only here | adapted |
| `seranking-seo-google` | Search Console, PageSpeed, CrUX, GA4, Keyword Planner access | Renamed from `seo-google`; needs SE Ranking MCP/API; text-only here | adapted |
| `seranking-seo-hreflang` | Hreflang / international SEO audit | Renamed from `seo-hreflang`; needs SE Ranking MCP/API; text-only here | adapted |
| `seranking-seo-images` | Image SEO audit (alt text, formats, ImageObject) | Renamed from `seo-images`; needs SE Ranking MCP/API; text-only here | adapted |
| `seo-keyword-cluster` | Intent clusters, pillar+spoke plan and internal-link map | needs SE Ranking MCP/API; text-only here | unchanged |
| `seo-keyword-niche` | Longtail and question keyword mining for a content tier | needs SE Ranking MCP/API; text-only here | unchanged |
| `seranking-seo-local` | Local SEO audit (GBP, NAP, citations) | Renamed from `seo-local`; needs SE Ranking MCP/API; text-only here | adapted |
| `seranking-seo-page` | URL-level keyword/traffic intelligence and keep/refresh/kill verdict | Renamed from `seo-page`; needs SE Ranking MCP/API; text-only here | adapted |
| `seranking-seo-plan` | Quarterly SEO roadmap | Renamed from `seo-plan`; needs SE Ranking MCP/API; text-only here | adapted |
| `seranking-seo-schema` | JSON-LD detection, validation and generation | Renamed from `seo-schema`; needs SE Ranking MCP/API; text-only here | adapted |
| `seranking-seo-sitemap` | Sitemap versus site-audit comparison | Renamed from `seo-sitemap`; needs SE Ranking MCP/API; text-only here | adapted |
| `seo-subdomain` | Subdomain ownership map and consolidation check | needs SE Ranking MCP/API; text-only here | unchanged |
| `seranking-seo-sxo` | SERP page-type and intent-mismatch diagnosis | Renamed from `seo-sxo`; needs SE Ranking MCP/API; text-only here | adapted |
| `seo-technical-audit` | One-shot technical SEO audit | needs SE Ranking MCP/API; text-only here | unchanged |
| `site-audit-to-social-distribution` | Audit findings turned into reviewed copy plus Planable distribution | needs SE Ranking MCP/API and Planable; text-only here | unchanged |

### J. AURORA SEO Intelligence (added 2026-11-03)

Source: authored in this repository for the SEO Intelligence agent
(`.claude/agents/seo-intelligence-agent.md`, `docs/seo-intelligence.md`). License: the
repository's. Dependencies: the `seo-data` MCP server (optional, free) and AURORA's own APIs; all
bookkeeping goes through `scripts/seo_intelligence.py` (no network). Which skill runs in which mode
is defined in `seo-intelligence/skills/skill-registry.json`, which also records each skill's
evidenced historical use on NextSteps. AURORA modules: 6 (SEO intelligence).

| Skill | What it covers | How Claude should use it | Status |
| --- | --- | --- | --- |
| `seo-audit` | `/seo-audit`: full SEO execution for a configured site, history comparison, report | Entry point; follows the registry plan for mode `audit`. **Replaces the name** of the claude-seo full-site audit, now `seo-full-site-audit` (content unchanged plus an AURORA note) | local |
| `seo-competitor-analysis` | `/seo-competitor-analysis`: SERP competitors, page comparison, gaps, dated snapshot | Entry point for mode `competitor-analysis` | local |
| `seo-content-optimization` | `/seo-content-optimization`: concrete edits to existing pages; branch-only implementation after approval | Entry point for mode `content-optimization` | local |
| `seo-performance-review` | `/seo-performance-review`: Search Console period comparison and change attribution | Entry point for mode `performance-review`; needs GSC data | local |
| `seo-update-skills` | `/seo-update-skills`: review, decide and apply skill proposals | User-invoked only (`disable-model-invocation`) | local |
| `website-seo-discovery` | Site profile, sitemap/robots, key pages, technical probes, read-only repository state | Discovery step | local |
| `seo-report-analysis` | Normalise uploaded SEO reports into findings | When report files are supplied | local |
| `seo-learning-and-memory` | Load/store knowledge, comparison rules, skill proposals | First and last step of every mode | local |

The AURORA planning skills `seo-keyword-research`, `seo-content-strategy` and `seo-quality-audit`
(set 1) were expanded on 2026-11-03 to the SEO Intelligence format (inputs, tools, storage,
improvement process); their original rules are kept.

## Rejected skills and why

| Repository | Skill | Reason |
| --- | --- | --- |
| firecrawl | `firecrawl-build-onboarding` | Its purpose is to obtain keys via a browser flow, run `npx firecrawl-cli init`, use Stripe Projects and write `FIRECRAWL_API_KEY` to `.env`; AURORA's rules forbid modifying `.env` and installing tooling, and the key is already configured by the developer. |
| anthropics/skills | `docx`, `pdf`, `pptx`, `xlsx` | Source-available, not open source (repository README); also already provided by this environment as `anthropic-skills:*`. |
| anthropics/skills | `claude-api`, `mcp-builder`, `web-artifacts-builder`, `canvas-design`, `algorithmic-art`, `brand-guidelines`, `theme-factory`, `internal-comms`, `slack-gif-creator`, `academy-guide`, `discernment-nudge` | Not about research, skill authoring or orchestration (`claude-api` is already provided as the `claude-api` skill in this environment). |
| langgraph | (no skills exist) | Framework repository; a local reference skill was authored instead and labelled as such. |
| serpapi-python | (no skills exist) | Client library repository; docs copied as references under a locally authored skill. |
| seo-agent-toolkit | `seo-migration`, `seo-change-review` | Repository/PR-diff and URL-migration tooling for site engineers; outside AURORA's research and intelligence scope. |
| seo-mastery-agent-skills | `seo-mastery-jp` | Japanese-language twin of `seo-mastery`; same content. |
| dataforseo/mcp-server-typescript | `dataforseo-mcp-server` (SKILL.md) | Tells the agent to run `npx dataforseo-mcp-server` in a shell with credentials in the environment. AURORA rules forbid installing tooling and bypassing the provider adapter. Revisit only as a reference for an adapter. |
| Patience-dot-devl/gocrawl | (no skills) | Go crawler with its own CLAUDE.md/AGENTS.md for developing itself. Running a separate crawler would bypass `UrlPolicy`/`SourceManager`. |
| microsoft/playwright | `.claude/skills/*` (api-review, playwright-dev, playwright-triage, and others) | Contributor skills for developing Playwright itself, not for using it. Browser checks would need Playwright as a dependency, which is not installed. |

## Duplicates and which to prefer

Duplicates were kept when they are complementary; names were prefixed so nothing was overwritten.

| Topic | Candidates | Guidance |
| --- | --- | --- |
| Keyword research | AURORA `seo-keyword-research` (SEO Intelligence keyword step), `seo-toolkit-keyword-research` (method + evidence contract), `claude-seo` `seo-cluster`/`seo-plan` (tool-backed) | Method questions: toolkit. Clustering by SERP overlap with live data: `seo-cluster`. The AURORA skill is the step SEO Intelligence runs; it defers to the toolkit for intent taxonomy. |
| SERP analysis | AURORA `serp-analysis`, `seo-toolkit-serp-analysis`, `serpapi-search-research` | Observed-SERP reasoning: toolkit. Request/response mechanics and cost: `serpapi-search-research`. |
| Content briefs | `seo-toolkit-content-brief`, `claude-seo` `seo-content-brief` | Toolkit for the evidence-contract specification; `claude-seo` when its competitor-scoring scripts are available through the plugin. |
| Schema / structured data | `seo-toolkit-schema`, `claude-seo` `seo-schema`, `seo-mastery/structured-data.md` | Toolkit for truthfulness rules; `seo-mastery` for full JSON-LD templates and retired rich-result notes; `claude-seo` for script validation when the plugin is installed. |
| Programmatic SEO | `seo-toolkit-programmatic`, `claude-seo` `seo-programmatic` | Toolkit for justification and index-bloat judgement; `claude-seo` for template planning. |
| Technical audit | `seo-toolkit-technical-audit`, `claude-seo` `seo-technical`, `seo-mastery/technical-seo.md` | Toolkit for the audit contract; `seo-mastery` for reference depth; `claude-seo` for tool-backed audits. |
| AI search visibility | `seo-toolkit-ai-visibility`, `claude-seo` `seo-geo`/`seo-agentic`, `seo-mastery/ai-search.md` | Toolkit for observation-based audits; `seo-mastery` for the evidence-level framework; `claude-seo` for workflows. |
| Skill authoring | project `skill-creator` (scripts omitted), plugin `anthropic-skills:skill-creator` | Same upstream content; use the plugin version when it is listed (it has the scripts). |
| Firecrawl | `firecrawl-build*`, AURORA `firecrawl-extraction` | AURORA's skill is the policy (SSRF, no provider-side `/crawl`); Firecrawl's are endpoint guidance. |

## Security and license review

Every selected SKILL.md and reference file was read before copying. Findings:

- No destructive commands and no instructions to read or exfiltrate secrets in the installed
  text. One skill makes a network call by design: `web-design-guidelines` tells the agent to fetch
  its rules from GitHub on every use (documented in its `ATTRIBUTION.md`). Firecrawl references and `seo-mastery/audit-workflow.md` contain
  installation or shell examples (`pip install firecrawl-py`, `npm install -g lighthouse`, curl
  loops); they are documentation and are not to be executed in AURORA. The one skill built around
  writing keys to `.env` (`firecrawl-build-onboarding`) was rejected.
- Only text files were copied (`.md`, `.txt`, `.json`, `.yml`); all Python/Node/HTML files were
  omitted and none were executed. A validation pass confirms 0 executables under `.claude/skills`.
- Licenses: MIT (seo-agent-toolkit, seo-mastery, serpapi-python, LangGraph), Apache-2.0
  (Anthropic skills), ISC declared per Firecrawl skill inside an AGPL-3.0 repository (no ISC text
  ships with the skill: verify before redistributing those four). All are compatible with keeping
  the files in this repository with attribution; the catalog and `ATTRIBUTION.md` files provide it.
- Skill text is untrusted input to Claude: it is guidance, cannot authorise external writes, and
  never overrides `CLAUDE.md`.

## Dependencies deliberately not installed

`firecrawl-py`, `serpapi`, `langgraph-checkpoint-postgres`, Node packages for the toolkit or
`claude-seo` scripts, Lighthouse, Playwright. `langgraph` 0.6.11 was already present.

Skills whose workflow cannot run until something outside the repository is set up:

| Skill | Needs | State |
| --- | --- | --- |
| `webapp-testing` | Playwright (Python `playwright` or npm `@playwright/test`) and its browsers | **Not installed** (checked: no Python module, no `frontend/node_modules/@playwright`, no `ms-playwright` browser cache). Microsoft Edge is present but is not a substitute for the Playwright driver |
| `seo-dataforseo` | DataForSEO MCP server + paid account | Not configured |
| `seo-gsc-diagnosis` | `gsc` MCP server + Search Console access | Not configured |
| `seo-image-gen` | `nanobanana` MCP server (Gemini) | Not configured |
| `seo-google` | Google API key / OAuth credentials | Not configured |
| `web-design-guidelines` | Network access to `raw.githubusercontent.com` at use time | Available, unpinned |
| `toolkit` + `claude-seo` script steps | The omitted `scripts/*.mjs` / Python scripts | Omitted by design; treat that evidence as `not_available` |

## Module and workflow mapping

Which AURORA module and user workflow each skill serves. Modules 1-6 are the planned scope;
Modules 7-8 (content generation, publishing, monitoring) are **not planned**, so skills that only
serve them are marked *out of scope* and should not drive implementation.

Workflow codes:
- `W1`: website research and intelligence.
- `W2`: crawling and structured fact extraction.
- `W3`: AI research and competitor comparison.
- `W4`: knowledge graph and evidence provenance.
- `W5`: SEO website audit.
- `W6`: keyword and SERP research.
- `W7`: content analysis and optimization.
- `W8`: prioritized SEO action plan.
- `DEV`: building AURORA itself (API, UI, tests, docs, skills).

Roles:
- **method**: a Module 6 analyzer implements the skill's checks (`docs/module-6-design.md` §2.2).
- **secondary**: an extra checklist for the same analyzer.
- **reference**: background depth only.
- **dev**: guidance for writing or testing code.
- **blocked**: needs a dependency listed above.
- **out of scope**: serves Modules 7-8.

| Skill | Module(s) | Workflows | Role |
| --- | --- | --- | --- |
| `web-research` | 2 | W1 | dev (planning) |
| `serp-analysis` | 2, 6 | W1, W6 | secondary |
| `fact-verification` | 3, 4 | W2, W3 | dev (planning) |
| `firecrawl-extraction` | 2, 3 | W1, W2 | dev (policy: SSRF, no provider `/crawl`) |
| `graphrag-research` | 5, 6 | W4 | dev (planning) |
| `knowledge-graph` | 5 | W4 | dev (planning) |
| `seo-keyword-research` | 6 | W6 | secondary |
| `seo-content-strategy` | 6 | W7 | secondary |
| `seo-quality-audit` | 6 | W7 | secondary |
| `seo-blog-writer` | 7 | — | out of scope |
| `wordpress-publishing` | 8 | — | out of scope |
| `firecrawl-build` | 2, 3 | W1, W2 | dev (endpoint choice for `FirecrawlTool`) |
| `firecrawl-build-scrape` | 2, 3, 6 | W2, W5 | dev (`scrape_for_seo` formats) |
| `firecrawl-build-search` | 2 | W1 | reference |
| `firecrawl-build-interact` | — | — | reference (no interactive fetching planned) |
| `langgraph-orchestration` | 6 | W8 | dev (only if decision D2 chooses LangGraph) |
| `serpapi-search-research` | 2, 6 | W1, W6 | dev (`search_serp` parsing, cost) |
| `skill-creator` | all | DEV | dev |
| `doc-coauthoring` | all | DEV | dev (docs) |
| `fastapi-patterns` | 1-6 | DEV (all API routes) | dev |
| `neo4j-cypher` | 5 | W4, DEV | dev (Cypher review under the AURORA note) |
| `react-best-practices` | UI | DEV (dashboard for W1-W8) | dev |
| `web-design-guidelines` | UI | DEV | dev (accessibility review; network fetch) |
| `frontend-design` | UI | DEV | dev |
| `webapp-testing` | UI | DEV (end-to-end checks of W1-W8) | blocked (Playwright) |
| `seo-toolkit-orchestrator` | 6 | W8 | method (planner rules, synthesis) |
| `seo-toolkit-strategy` | 6 | W3, W8 | method (competitor analysis) |
| `seo-toolkit-prioritization` | 6 | W8 | method (recommendation rubric) |
| `seo-toolkit-keyword-research` | 6 | W6 | method |
| `seo-toolkit-serp-analysis` | 6 | W6 | method |
| `seo-toolkit-content-brief` | 6 | W6, W7 | method (content gap) |
| `seo-toolkit-content-optimizer` | 6 | W7 | method |
| `seo-toolkit-page-audit` | 6 | W5, W7 | method (single page) |
| `seo-toolkit-technical-audit` | 6 | W5 | method |
| `seo-toolkit-schema` | 6 | W5 | method |
| `seo-toolkit-internal-linking` | 6 | W5 | method |
| `seo-toolkit-cannibalization` | 6 | W5, W6 | method |
| `seo-toolkit-ai-visibility` | 6 | W5 | method |
| `seo-toolkit-programmatic` | 6 | W5 | reference |
| `seo-toolkit-indexation-analysis` | 6 | W5 | blocked (needs Search Console data) |
| `seo-toolkit-performance-analysis` | 6 | W8 | blocked (needs Search Console data) |
| `seo-mastery` | 6 | W5, W7 | reference |
| `seo-gsc-diagnosis` | 6 | W5 | blocked (gsc MCP / Search Console) |
| `seo` | 6 | W5, W8 | secondary (hub) |
| `seo-full-site-audit` | 6 | W5 | secondary (optional step of `/seo-audit`) |
| `seo-audit` | 6 | W5, W8 | method (SEO Intelligence orchestrator) |
| `seo-competitor-analysis` | 6 | W3, W6, W8 | method (SEO Intelligence mode) |
| `seo-content-optimization` | 6 | W7 | method (SEO Intelligence mode) |
| `seo-performance-review` | 6 | W8 | method (needs Search Console data) |
| `seo-update-skills` | 6 | DEV | dev (skill maintenance, user-invoked) |
| `website-seo-discovery` | 6 | W5 | method |
| `seo-report-analysis` | 6 | W5 | method |
| `seo-learning-and-memory` | 6 | W5-W8 | method (knowledge base) |
| `seo-technical` | 6 | W5 | secondary |
| `seo-page` | 6 | W5, W7 | secondary |
| `seo-schema` | 6 | W5 | secondary |
| `seo-sitemap` | 6 | W5 | secondary |
| `seo-hreflang` | 6 | W5 | secondary |
| `seo-images` | 6 | W5 | secondary |
| `seo-agentic` | 6 | W5 | secondary (AI-agent access) |
| `seo-geo` | 6 | W5, W7 | secondary (AI search) |
| `seo-content` | 6 | W7 | secondary |
| `seo-content-brief` | 6 | W6, W7 | secondary |
| `seo-cluster` | 6 | W6 | secondary (SERP-overlap clustering) |
| `seo-sxo` | 6 | W6 | secondary (intent) |
| `seo-plan` | 6 | W8 | secondary |
| `seo-flow` | 6 | W8 | secondary |
| `seo-competitor-pages` | 6 | W3 | reference (comparison analysis only; page generation is out of scope) |
| `seo-drift` | 6 | W5 | reference (change tracking deferred) |
| `seo-programmatic` | 6 | W5 | reference |
| `seo-dataforseo` | 6 | W6 | blocked (DataForSEO) |
| `seo-google` | 6 | W5 | blocked (Google credentials) |
| `seo-backlinks` | — | — | reference (no backlink data source) |
| `seo-local` | — | — | reference (no GBP data source) |
| `seo-maps` | — | — | reference (no maps data source) |
| `seo-ecommerce` | — | — | reference (no merchant data source) |
| `seo-image-gen` | 7 | — | out of scope (asset generation; MCP) |
| `ai-search-gaps-to-social-campaign` | 6 | — | out of scope (Planable social publishing; no SE Ranking/Planable access) |
| `client-onboarding-proposal` | 6 | — | out of scope (Planable social publishing; no SE Ranking/Planable access) |
| `local-gmb-visibility` | 6 | — | out of scope (Planable social publishing; no SE Ranking/Planable access) |
| `seo-ads` | 6 | — | reference (needs SE Ranking MCP/API; not configured) |
| `seo-agency-landing-page` | 6 | — | reference (needs SE Ranking MCP/API; not configured) |
| `seo-ai-search-share-of-voice` | 6 | — | reference (needs SE Ranking MCP/API; not configured) |
| `seo-ai-social-report` | 6 | — | out of scope (Planable social publishing; no SE Ranking/Planable access) |
| `seo-api` | 6 | — | reference (needs SE Ranking MCP/API; not configured) |
| `seo-backlink-gap` | 6 | — | reference (needs SE Ranking MCP/API; not configured) |
| `seo-backlinks-profile` | 6 | — | reference (needs SE Ranking MCP/API; not configured) |
| `seo-competitor-gap-analysis` | 6 | — | reference (needs SE Ranking MCP/API; not configured) |
| `seranking-seo-competitor-pages` | 6 | — | reference (needs SE Ranking MCP/API; not configured) |
| `seo-content-audit` | 6 | — | reference (needs SE Ranking MCP/API; not configured) |
| `seranking-seo-content-brief` | 6 | — | reference (needs SE Ranking MCP/API; not configured) |
| `seranking-seo-drift` | 6 | — | reference (needs SE Ranking MCP/API; not configured) |
| `seo-firecrawl` | 6 | — | reference (needs SE Ranking MCP/API; not configured) |
| `seo-gaps-to-social-campaign` | 6 | — | out of scope (Planable social publishing; no SE Ranking/Planable access) |
| `seranking-seo-geo` | 6 | — | reference (needs SE Ranking MCP/API; not configured) |
| `seranking-seo-google` | 6 | — | reference (needs SE Ranking MCP/API; not configured) |
| `seranking-seo-hreflang` | 6 | — | reference (needs SE Ranking MCP/API; not configured) |
| `seranking-seo-images` | 6 | — | reference (needs SE Ranking MCP/API; not configured) |
| `seo-keyword-cluster` | 6 | — | reference (needs SE Ranking MCP/API; not configured) |
| `seo-keyword-niche` | 6 | — | reference (needs SE Ranking MCP/API; not configured) |
| `seranking-seo-local` | 6 | — | reference (needs SE Ranking MCP/API; not configured) |
| `seranking-seo-page` | 6 | — | reference (needs SE Ranking MCP/API; not configured) |
| `seranking-seo-plan` | 6 | — | reference (needs SE Ranking MCP/API; not configured) |
| `seranking-seo-schema` | 6 | — | reference (needs SE Ranking MCP/API; not configured) |
| `seranking-seo-sitemap` | 6 | — | reference (needs SE Ranking MCP/API; not configured) |
| `seo-subdomain` | 6 | — | reference (needs SE Ranking MCP/API; not configured) |
| `seranking-seo-sxo` | 6 | — | reference (needs SE Ranking MCP/API; not configured) |
| `seo-technical-audit` | 6 | — | reference (needs SE Ranking MCP/API; not configured) |
| `site-audit-to-social-distribution` | 6 | — | out of scope (Planable social publishing; no SE Ranking/Planable access) |

## Validation

`scripts/validate_skills.py` validates **all** installed skills. It reads files only: nothing in a
skill folder is imported or executed, and the network is used only with `--verify-remote`.

```powershell
.venv\Scripts\python scripts\validate_skills.py                  # offline; exit 1 on errors
.venv\Scripts\python scripts\validate_skills.py --verbose        # include info-level findings
.venv\Scripts\python scripts\validate_skills.py --json           # machine-readable
.venv\Scripts\python scripts\validate_skills.py --verify-remote  # also check commits on GitHub
```

Checks:
- **Frontmatter:** YAML parses with `safe_load`; `name` matches the directory and the naming rules;
  `description` is present, ≤ 1024 characters and has no tags.
- **Provenance:**
  - External skills have `ATTRIBUTION.md` with a GitHub repository and a 40-character commit.
  - There is a license file or a declared license.
  - `claude-seo` provenance lives in this catalog.
- **Files:** no executables, shebangs or symlinks; size caps.
- **Links:** relative links resolve, links to sibling skills point to installed skills, and no
  link leaves `.claude/skills/`.
- **Risky mentions:** references to omitted scripts, install commands, `.env` writes, external
  writes, runtime fetches and MCP dependencies are reported. Lines that forbid the action are not
  flagged.
- **AURORA notes:** required notes are present, including the Module 5 rules in `neo4j-cypher` and
  coverage of its APOC, Cypher 25, delete and bulk-write conflicts.
- **Dependencies:** dependencies a skill needs (Playwright) are installed.
- **Catalog:** every skill is mentioned here and has a row in the mapping above.

Result on 2026-10-31 (after the corrections in section H):
- **Errors:** 0.
- **Commits:** all 8 distinct recorded commits exist on GitHub. Seven were confirmed by the
  script's commit lookup. `dc8367e` (the corrected Vercel commit) was confirmed through the GitHub
  commit history for the skill's path, because a later full run hit GitHub's anonymous limit of 60
  requests an hour. The script now makes one lookup per distinct commit.
- **Warnings that remain** are expected and documented above:
  - Omitted scripts referenced by the toolkit, `neo4j-cypher`, `react-best-practices` and
    `webapp-testing`.
  - Missing Playwright.
  - Missing MCP servers.
  - The runtime fetch in `web-design-guidelines`.
  - Links to Firecrawl skills that were not installed (`firecrawl-build-onboarding`, rejected;
    `firecrawl-research-index` and `firecrawl-developer-index`, not imported).
  - Broken internal links that also exist upstream (`react-best-practices/AGENTS.md`, the copied
    SerpApi docs, `seo-flow`'s repository-level image).
