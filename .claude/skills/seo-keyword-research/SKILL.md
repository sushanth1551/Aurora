---
name: seo-keyword-research
description: "Organize keyword opportunities from validated data sources. Planning guidance only; AURORA provider integrations are not implemented yet."
---

# seo-keyword-research

**Description:** Organize keyword opportunities from validated data sources.

## Purpose
Build a prioritized keyword set aligned to search intent and business scope.

## When to use it
Use when the user requests keyword discovery, clustering, or prioritization and
reliable keyword/SERP evidence is available.

## Required inputs
- Topic, audience, locale, and business context.
- Search terms and provider metrics, if available.
- Constraints such as product focus or excluded topics.

## Execution workflow
1. Identify seed concepts and audience needs.
2. Gather candidate terms from explicitly identified sources.
3. Group by intent and topic; keep evidence and metric source attached.
4. Prioritize using available evidence and the user's goals.
5. Mark estimates, missing metrics, and assumptions.

## Expected outputs
Keyword clusters, intent labels, prioritization rationale, source metadata, and
follow-up research needs.

## Error handling
Do not fabricate volume, difficulty, or CPC. Flag provider gaps and return
qualitative findings only when quantitative data is unavailable.

## Quality requirements
Avoid keyword stuffing, respect locale and language, account for intent, and
state clearly that prioritization does not guarantee traffic or rank.
