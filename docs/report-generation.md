# Report generation

Code: `backend/app/intelligence/report_generator.py`; schema `IntelligenceReport`
(`intelligence/models.py`); table `intelligence_reports`.

## Types
`executive_summary`, `detailed_report`, `company_intelligence`, `product_comparison`,
`technology_analysis`, `competitor_intelligence`, `market_research`. The type selects the
title, which comparisons are included and how many key findings appear.

## Schema
Report id, research job id, analysis id, version, title, objective, executive summary,
methodology, scope, key findings, company/product/technology analysis, comparisons,
competitor analysis, trends, opportunities, threats, contradictions, information gaps,
conclusions, source references, research date, metadata (provider, model, prompt
version, mock flag, context id/hash, iterations, stop reason, follow-ups, usage snapshot),
quality metrics and limitations.

## Rendering
- **JSON:** `IntelligenceReport.model_dump_json()`; schema-valid by construction.
- **Markdown:** `render_markdown()`, rendered deterministically from the schema. All text,
  including model output and source content, is whitespace-collapsed and every Markdown/HTML
  special character (`\ ` `` ` `` `* _ { } [ ] < > | # ~`) is backslash-escaped. Model or
  source text therefore cannot create links, HTML or script. Source URLs appear only in
  code spans.
- **PDF:** `PdfExporter` exists behind the `ReportExporter` interface but raises
  `ConfigurationException`. No PDF dependency was added.

## Citations
A citation is a stable evidence id (`c-…`/`s-…`). The **Source references** section lists
every cited id with its stored URL, title, domain, official/third-party flag,
verification status and excerpt. URLs come only from stored sources, never from model
output. Disclosed contradictions are always included in the source references.

## Standing limitations
Every report states that findings reflect only the collected sources at the research
date, that company websites are self-reported, and that the quality checks are not a
measure of factual accuracy. Mock-provider reports carry an explicit warning first.
