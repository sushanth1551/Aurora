# LLM usage and cost management

Code: `backend/app/llm/usage_tracker.py`; table `llm_usage_records`.

## What is measured vs estimated
- **Measured:** input, output and cached tokens exactly as the provider reports them. If
  the provider omits a value it stays `null`; counts are never estimated or invented.
- **Estimated:** money. `estimated_cost` is computed from a pricing registry and is
  `null` ("unavailable") when the model has no configured price or token counts are
  missing. `cost_available` and `cost_complete` say whether totals cover every request.
  Estimates can differ from provider invoices (rounding, discounts, tiers).

## Pricing registry
No prices are built into the code, because prices change. Configure them from each
provider's official pricing page:

```
LLM_PRICING_JSON={"openai:<model-id>": {"input_per_million": 0.0, "output_per_million": 0.0, "cached_input_per_million": 0.0, "currency": "USD"},
                  "anthropic:<model-id>": {"input_per_million": 0.0, "output_per_million": 0.0}}
```

Keys are `provider:model` prefixes; the longest matching prefix wins. Cached input uses
`cached_input_per_million` when present, otherwise the input rate. Update the JSON
(no code change) whenever a provider changes its prices.

## Groq pricing and limits (checked against Groq's docs on 2026-10-31; unverified against an invoice)
Source: Groq's model page `console.groq.com/docs/model/openai/gpt-oss-20b`, which lists
USD per 1M tokens: input **$0.075**, cached input **$0.037**, output **$0.30**. The
`groq.com/pricing` page did not expose per-model figures when checked, so the model page is
the source. Groq states cached input is billed at a 50% discount (0.075 × 0.5 = 0.0375,
which the page rounds to 0.037). Use the value the page shows.

```
LLM_PRICING_JSON={"groq:openai/gpt-oss-20b": {"input_per_million": 0.075, "output_per_million": 0.30, "cached_input_per_million": 0.037, "currency": "USD"}}
```

**Free tier vs Developer tier.** The official rate-limit page gives the *Developer plan base
limits* for `openai/gpt-oss-20b`: **30 RPM, 1 000 RPD, 8 000 TPM, 200 000 TPD**. It does not
list separate Free-plan values for this model. Free-plan figures I found were only from
third-party sites (for other models) and are not used here. Check your account's limits page.
The prices above are pay-per-token. Whether a Free plan has any cost was not confirmed from
an official source.

**What this means for AURORA.** TPM is the tight limit: 8 000 tokens per minute is less than
the global evidence budget (`INTELLIGENCE_CONTEXT_MAX_TOKENS=12000`). Oversized requests
return 429, and retries then stall the run. That is why the Groq-specific budget
(`INTELLIGENCE_CONTEXT_MAX_TOKENS_GROQ`) defaults to 4 000; raise it once your account's
limits page shows more headroom. Expect a 200 000-token daily cap to fit only a few full
analyses. Reasoning tokens are assumed
to be billed in `completion_tokens` (the usage fields Groq documents do not break them out).
This is not verified.

## Budgets (per analysis)
| Setting | Default | Effect |
| --- | --- | --- |
| `INTELLIGENCE_MAX_LLM_CALLS` | 15 | requests (including failed ones) |
| `INTELLIGENCE_MAX_TOKEN_BUDGET` | 50 000 | reported tokens used + estimated prompt size of the next request |
| `INTELLIGENCE_MAX_ESTIMATED_COST` | unset | estimated money; **requires** a price for the model, otherwise every request is refused (fail closed) |

The tracker checks the budget **before** each request. An exhausted budget raises
`LLMBudgetError`, nothing is sent, and the engine degrades: reasoning stops, synthesis
falls back to deterministic tables, and the analysis is `partially_completed`. Requests
cannot raise these limits.

## Records
Each request stores provider, model, operation, prompt version, token counts, duration,
estimated cost, success/error category, fallback info and timestamp, linked to the
analysis and its research job. Prompts and responses are **not** stored.
`GET /api/v1/intelligence/{job_id}/usage` returns per-request entries and totals.

## Context budgets per provider
The research context is bounded by an *evidence-token budget*, resolved per provider by
`Settings.context_budget_for(name)`:

| Setting | Default | Applies to |
| --- | --- | --- |
| `INTELLIGENCE_CONTEXT_MAX_TOKENS` | 12 000 | global value, used when a provider has no override |
| `INTELLIGENCE_CONTEXT_MAX_TOKENS_GROQ` | 4 000 | Groq |
| `INTELLIGENCE_CONTEXT_MAX_TOKENS_OPENAI` | unset -> global | OpenAI |
| `INTELLIGENCE_CONTEXT_MAX_TOKENS_ANTHROPIC` | unset -> global | Anthropic |
| `INTELLIGENCE_CONTEXT_MAX_TOKENS_MOCK` | unset -> global | mock (development) |

Rules:
- The provider that will answer determines the budget. With provider fallback enabled the
  context is built once and may be answered by either provider, so the **smaller** of the
  two budgets is used and recorded as `"<name> (fallback, smaller)"`.
- Follow-up research rebuilds the context with the same budget.
- These are application budgets for the `<evidence>` block, not provider context windows
  and not rate limits (none are hardcoded; the Groq default is a cautious start).
- The effective budget, its source, the tokens that fit, the evidence items considered and
  whether evidence was trimmed are recorded on the analysis
  (`configuration.context_token_budget`, `progress.context_*`, `progress.evidence_trimmed`),
  and a trimmed report says so in its limitations. Evidence ids, URLs and excerpts are
  unchanged by trimming; lower-ranked items are simply left out.

**What the budget includes.** Only the selected evidence (about 4 characters per token plus
a small per-item allowance for the tags). The system prompt (~250 tokens), the task
instructions, the escaped objective and the engine-built sections (numbered questions,
known gaps, findings) are added on top of it for every request, and the model's output (up
to `LLM_MAX_OUTPUT_TOKENS`, default 4 096) comes after that. A Groq request with a
4 000-token evidence budget is therefore roughly 4 500-5 000 prompt tokens plus output. The
pre-flight check against `INTELLIGENCE_MAX_TOKEN_BUDGET` does estimate the whole prompt
(system prompt and task text included), but provider per-minute limits count output too, so
lower `LLM_MAX_OUTPUT_TOKENS` as well if a limit is hit.
