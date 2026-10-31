# Multi-source synthesis

Code: `backend/app/intelligence/synthesis_engine.py`, `confidence.py`.

```mermaid
flowchart LR
    C[context evidence] --> B[deterministic baseline<br/>company / product / technology tables]
    B --> CF[common features: same value for ≥2 entities]
    B --> DF[differentiators: value for 1 entity<br/>+ "may be missing evidence" caveat]
    C --> L1[LLM comparison layer<br/>positioning, strengths, advantages, limitations]
    C --> L2[LLM market layer<br/>trends, opportunities, threats, conclusions]
    L1 & L2 --> V[validation:<br/>known ids only · FACT w/o evidence → HYPOTHESIS ·<br/>rankings → HYPOTHESIS · trend needs ≥2 items from ≥2 sources]
    B & V --> S[executive summary<br/>(LLM, or top verified facts)]
```

## Baseline (no LLM needed)
Claims are grouped by entity and dimension: products, services, pricing, target customers,
headquarters, industry, technologies *mentioned*, integrations, features and use cases.
Supported or partially supported claims become `fact` statements; unverified claims become
`observation`s marked "may be a self-reported marketing statement". Conflicting claims are
left out of the tables and listed as contradictions. Every statement cites its claim.

## Validated LLM layer
- **Facts vs observations vs hypotheses vs recommendations** are separate `FindingKind`s.
- **Strengths/weaknesses:** documented strengths, evidence-supported comparative advantages
  (evidence for both sides), observed limitations (stated limits only), missing public
  information and analyst hypotheses are separate lists. Missing information is never
  treated as a weakness.
- **Opportunities/threats:** description, supporting evidence, related entities, rationale,
  confidence and limitations. With no valid evidence they are marked `is_hypothesis`.
- **Rankings:** superlatives such as best, leading, #1, market leader or better than
  downgrade a fact to a hypothesis.
- **Trends:** at least two valid evidence items from at least two sources; otherwise the
  trend is discarded and the reason recorded.
- If the LLM fails or a budget is exhausted, the step is skipped, the result is marked
  `llm_degraded`, and the limitation is recorded.

## Confidence (separate components)
`extraction` (mean claim confidence), `source_credibility`, `evidence_support`
(directness), and `analytical`: the model's stated level, **capped** by a rubric. HIGH
needs ≥3 items from ≥2 domains with support ≥0.7; MEDIUM needs ≥2 items; otherwise LOW.
`overall` is the weakest component. The model can lower its confidence but cannot raise
it above the rubric.
