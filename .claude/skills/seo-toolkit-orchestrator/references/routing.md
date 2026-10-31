<!-- Generated from shared/routing/routing-matrix.json. Do not edit by hand. -->

# Routing matrix

Use this reference when the request spans more than one SEO concern.

| User outcome | Primary skills | Useful additions |
| --- | --- | --- |
| Full assessment or multi-domain request | seo-orchestrator | seo-strategy, seo-prioritization, seo-technical-audit, seo-migration, seo-performance-analysis |
| Full SEO strategy | seo-strategy, seo-prioritization | seo-technical-audit, seo-page-audit, seo-keyword-research, seo-performance-analysis |
| General site audit | seo-technical-audit | seo-page-audit, seo-indexation-analysis |
| Single URL review | seo-page-audit | seo-schema, seo-internal-linking, seo-serp-analysis |
| Site or domain migration | seo-migration | seo-technical-audit, seo-change-review, seo-indexation-analysis |
| Ranking opportunity | seo-keyword-research, seo-serp-analysis | seo-content-brief, seo-prioritization |
| New article or landing page | seo-content-brief | seo-keyword-research, seo-serp-analysis, seo-internal-linking |
| Improve existing content | seo-content-optimizer | seo-page-audit, seo-performance-analysis |
| Site architecture | seo-internal-linking, seo-technical-audit | seo-cannibalization |
| Possible duplicate targeting | seo-cannibalization | seo-keyword-research, seo-serp-analysis, seo-internal-linking |
| Structured data | seo-schema | seo-page-audit, seo-technical-audit |
| Scaled page proposal | seo-programmatic | seo-technical-audit, seo-indexation-analysis, seo-prioritization |
| Indexing change or anomaly | seo-indexation-analysis | seo-technical-audit, seo-performance-analysis |
| Search or traffic change | seo-performance-analysis | seo-indexation-analysis, seo-page-audit, seo-serp-analysis |
| Code, deployment or SEO specification change | seo-change-review | seo-technical-audit, seo-schema, seo-internal-linking |
| AI search access, observed mentions and citation changes | seo-ai-visibility | seo-content-optimizer, seo-schema, seo-technical-audit |

## Selection rules

1. Identify the requested outcome, scope and available evidence before selecting skills.
2. Prefer the smallest set that answers the request.
3. Add a specialist only when its input exists, or when its missing input is itself the point of the diagnosis.
4. Do not route from a keyword alone when the user intent is ambiguous.
5. When the host cannot load another skill, continue with the routing plan and available instructions; never imply the missing skill ran.

## Boundaries

- **seo-orchestrator** — Sequencing, cross-domain synthesis, conflict resolution and final prioritization. It does not perform specialist analysis and it is not a routing runtime.
- **seo-strategy** — Goals, constraints, positioning and the site model. Not raw crawl diagnosis.
- **seo-prioritization** — Sequencing, effort, risk and trade-offs across findings that already exist. It does not generate findings.
- **seo-technical-audit** — System-wide crawl and indexability mechanics, including delivery performance signals such as Core Web Vitals.
- **seo-page-audit** — One page and its observable page-level signals, across technical, content and intent.
- **seo-migration** — URL mapping, redirect behavior, before/after comparison and rollback evidence.
- **seo-change-review** — SEO impact of a code, deployment or specification change.
- **seo-keyword-research** — Query-to-topic mapping and page targeting.
- **seo-serp-analysis** — Observed result-page composition and dominant intent, including generative surfaces when data exists.
- **seo-content-brief** — Specification for content that does not exist yet.
- **seo-content-optimizer** — Editorial deltas to existing content. Never technical infrastructure.
- **seo-internal-linking** — Link architecture, hubs, orphans, anchors and depth.
- **seo-cannibalization** — Two or more existing URLs competing for one intent.
- **seo-schema** — Structured data detection, validation and truthful generation.
- **seo-programmatic** — Scaled page generation and preventing scaled low-value pages.
- **seo-indexation-analysis** — Indexation state and its evolution across supplied snapshots or datasets.
- **seo-performance-analysis** — Search and analytics outcome trends (clicks, impressions, CTR, position, sessions) from supplied datasets. Not page speed; delivery performance belongs to seo-technical-audit.
- **seo-ai-visibility** — Owns AI crawler access evidence and observed answer samples over time. General result-page intent belongs to seo-serp-analysis; passage editing belongs to seo-content-optimizer.

The matrix is a decision aid, not a promise that every host can invoke or sequence skills automatically.
