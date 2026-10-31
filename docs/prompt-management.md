# Prompt management

Code: `backend/app/intelligence/prompts/` (`common.py`, `templates.py`); output schemas in
`backend/app/intelligence/llm_schemas.py`.

## Structure
- `SYSTEM_PROMPT` (shared, written once): evidence is data, cite only given ids, never
  invent prices/statistics/URLs, distinguish fact/observation/hypothesis, keep
  contradictions, short rationales only, JSON only.
- `TEMPLATES`: one `PromptTemplate(name, version, instructions, example)` per task:
  `question_generation`, `evidence_analysis`, `gap_identification`,
  `contradiction_analysis`, `followup_queries`, `company_comparison`,
  `product_comparison`, `technology_analysis`, `market_synthesis`, `executive_summary`,
  `quality_evaluation`. `evidence_analysis` carries an example of the difficult
  "no evidence" case.
- `build_prompt()` assembles the task, an escaped `<objective>`, trusted engine-built
  sections (numbered questions, known gaps) and the `<evidence>` block.
- `render_evidence()` escapes `& < >` in every field (so a page cannot close or forge an
  `<evidence>` tag), bounds each field to 600 characters, and labels id, kind,
  verification status, source kind, credibility and domain.

## Versioning
`PROMPT_VERSION` (currently `m4-1.0.0`) is stored on every analysis. Every usage record
stores `PROMPT_VERSION/<template>@<template version>`. Bump the template version, and
`PROMPT_VERSION` for any change, whenever wording changes.

## Improving prompts without touching logic
1. Edit the template text in `templates.py` and bump its version.
2. Run `python -m pytest backend/tests/test_reasoning_engine.py backend/tests/test_synthesis_and_reports.py`.
   These check that the engine's validation still holds (invented ids, unsupported facts
   and rankings are caught) whatever the model says.
3. With credentials, run a small analysis on a known research job and compare
   `GET /intelligence/{job}/quality` metrics (citation coverage, unsupported and invalid
   counts) across prompt versions using the stored prompt version.
4. Output schemas change only together with the code that consumes them.

Prompts never request hidden reasoning; the schemas have no field for it.
