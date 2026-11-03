---
name: seo-content-strategy
description: "Turn keyword, competitor and performance evidence into a measurable content plan for a configured website: which existing pages to extend, consolidate or refresh, which new pages are justified, in what order, and how success will be checked. Use after research evidence exists, or to produce a clearly labeled strategy hypothesis; used by /seo-competitor-analysis. Never guarantees search outcomes."
metadata:
  author: AURORA
  version: "2.0.0"
  category: seo-intelligence
---

# seo-content-strategy

**Description:** Turn research evidence into a measurable content plan.

## Purpose
Develop a strategy connecting audience needs, evidence, keywords, and editorial goals without
guaranteeing search outcomes, and keep it as a dated record that later runs can check.

## When to use it
Use after research and keyword evidence is available or when producing a clearly labeled
strategy hypothesis. In `/seo-competitor-analysis` it turns gaps into a plan.

## Required inputs
- Business goals, audience, market, and locale (website config `conversion_goals`, `audiences`).
- Research findings and their sources (keyword and competitor snapshots).
- Keyword clusters or an explicit note that keyword evidence is missing.
- Implemented changes and rejected recommendations, so the plan does not repeat them.

## Tools and integrations
Reads the knowledge base through `seo-learning-and-memory`; no provider calls of its own.

## Execution workflow
1. Confirm goal, audience, and constraints.
2. Map evidence and intent to content opportunities.
3. Decide the action per opportunity, in this order of preference: refresh or extend the owner
   page, consolidate competing pages (with redirects), then create a new page.
4. Define topic clusters, formats, and proposed internal relationships (hub and spokes).
5. Prioritize by rationale, effort, and evidence strength (`seo-toolkit-prioritization`).
6. Define measurable outcomes per item (the metric, the source, the review date) and review
   assumptions with the user.
7. Compare with the previous strategy record: done, dropped, still open.

## Expected outputs
Prioritized themes, audience needs, keyword mapping, proposed formats and actions (refresh,
consolidate, new), KPIs with review dates, and evidence gaps.

## Quality requirements
Recommend useful, original content for people first; disclose uncertainty; do not imply
guaranteed rankings or traffic. No plan item may contradict a rejected recommendation without
new evidence.

## Error handling
Escalate conflicting goals or insufficient evidence; label assumptions rather than presenting
them as established facts.

## Website-specific configuration
Goals, audiences and editorial rules come from the website config.

## How findings are stored
A `research` snapshot of kind `content-strategy` plus one finding per planned item
(`content-plan:<topic>`), via `seo-learning-and-memory`.

## Improving this skill
Propose changes through `seo-learning-and-memory`, backed by performance reviews of earlier
plan items.
