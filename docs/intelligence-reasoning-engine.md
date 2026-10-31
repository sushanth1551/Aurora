# Intelligence reasoning engine

Code: `backend/app/intelligence/reasoning_engine.py`, `followup.py`.

```mermaid
flowchart TD
    A[context v1] --> Q[question generation<br/>(defaults if the LLM fails)]
    Q --> L{iteration ≤ max<br/>and before deadline?}
    L -->|no| STOP
    L -->|yes| AN[evidence analysis:<br/>findings, gaps, contradictions]
    AN --> V[validate ids; FACT without evidence → HYPOTHESIS;<br/>contradiction needs ≥2 valid ids]
    V --> D{sufficient or no gaps?}
    D -->|yes| STOP[stop + decision record]
    D -->|no| FU{follow-up enabled,<br/>quota left, not last iteration?}
    FU -->|no| STOP
    FU -->|yes| P[propose queries]
    P --> VAL[validate: length, no URLs/markup/instructions,<br/>in scope, not duplicate, per-iteration and total caps]
    VAL --> EXE[WebsiteIntelligenceService.run<br/>(existing SSRF, robots, crawl limits)]
    EXE --> RB[rebuild context v+1 over all job ids]
    RB --> L
```

## Stop conditions (always one of)
`sufficient`, `no_gaps`, `max_iterations`, `max_duration`, `max_queries`,
`budget_exhausted`, `followup_disabled`, `no_valid_followups`, `followup_failed`,
`llm_error`. Iterations are a bounded `for` loop; there is no unbounded path.

| Control | Setting | Default |
| --- | --- | --- |
| iterations | `INTELLIGENCE_MAX_ITERATIONS` | 3 |
| follow-ups per iteration | `INTELLIGENCE_MAX_FOLLOWUP_QUERIES` | 5 |
| follow-ups in total | `INTELLIGENCE_MAX_TOTAL_QUERIES` | 10 |
| wall-clock | `INTELLIGENCE_MAX_DURATION_SECONDS` | 300 (+30 s hard cancel) |
| LLM calls / tokens / cost | see `llm-cost-management.md` | 15 / 50 000 / unset |
| sources | `INTELLIGENCE_MAX_SOURCES` | 40 |

## Follow-up research safety
The model only *proposes* plain query text and an optional company/product name. It
never supplies URLs, tools or code. Queries are rejected if they are longer than 200
characters, contain URLs, markup, control characters or instruction-like text, are out
of scope (no term shared with the objective or known entities), or are duplicates.
Accepted queries run through `WebsiteFollowUpExecutor` → `WebsiteIntelligenceService.run`
(at most 3 websites × 8 pages each), so SerpApi/Firecrawl are reused, not duplicated.
Follow-up spends provider credits, so it needs **both** the request flag
`allow_follow_up_research=true` and `INTELLIGENCE_ENABLE_FOLLOWUP_RESEARCH=true`.
One failed query does not end the run.

## Outputs
Questions (with deterministically mapped evidence and statuses), findings (with
confidence breakdown), information gaps (deterministic + LLM), contradictions
(deterministic + validated LLM), follow-up queries (with status and rejection reason),
decision records (iteration, decision, stop reason, short rationale) and unresolved
uncertainties. Prompts never ask for step-by-step reasoning, and no reasoning traces are
requested or stored.
