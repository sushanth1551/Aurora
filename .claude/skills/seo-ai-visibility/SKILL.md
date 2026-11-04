---
name: seo-ai-visibility
description: "SEO Intelligence AI-search visibility for a configured website: AI crawler access per user agent and purpose (answer/search crawlers such as OAI-SearchBot, PerplexityBot, Claude-SearchBot vs training crawlers such as GPTBot, CCBot, Google-Extended) with RFC 9309 robots.txt matching, llms.txt validity, Google AI Overview presence and citations from SERP checks, and a prompt panel that turns recorded ChatGPT/Perplexity/Gemini/AI Mode answers into share of voice and prompt gaps. Use for /seo-ai-visibility, 'are we cited by ChatGPT/Perplexity', 'AI Overviews', 'GEO', 'can AI crawlers read my site'."
user-invocable: true
argument-hint: "[site-id]"
metadata:
  author: AURORA
  version: "1.0.0"
  category: seo-intelligence
---

# /seo-ai-visibility

## When to activate

"Do ChatGPT/Perplexity/Gemini mention us", "AI Overview", "GEO/AEO", "AI crawlers", "llms.txt",
or as the AI step of `/seo-audit`.

## Required inputs

Website id; `ai_visibility.prompts` (the questions students ask AI assistants);
`ai_visibility.allow_training_crawlers` (owner policy: true, false or unset).

## Tools and integrations

- `scripts/seo_insights.py ai-visibility <site>`: robots.txt and llms.txt through `UrlPolicy`
  (free), AI Overview data from the latest `seo-rank-tracking --serp` run (<= 30 days old), and
  the recorded answers.
- `scripts/seo_insights.py record-ai-answer <site> --prompt ... --engine chatgpt|perplexity|gemini|ai-mode|copilot|claude --date YYYY-MM-DD --cited a.com,b.com [--mention|--no-mention] [--excerpt ...] [--observed-by ...]`
  records an answer someone observed. AURORA does not call those engines. With Claude in Chrome
  connected and the owner's permission, the browser may be used read-only to ask the prompt and
  record what was shown.

## Execution steps

1. Run the command; read `insights/ai-visibility-latest.json`.
2. Report: blocked answer crawlers (high), training-crawler policy, llms.txt, AI Overview
   citations, share of voice per engine and prompts where competitors are cited instead.
3. For prompt gaps, hand over to `seo-geo` / `seo-content-optimization` for passage-level fixes
   (direct answer, sourced numbers, clear headings, dates).

## Analysis methodology

- Purpose matters: blocking answer/search crawlers removes the site from AI answers; blocking
  training crawlers does not. Googlebot governs AI Overviews and AI Mode.
- Share of voice uses the latest answer per prompt and engine; it is a sample, not a market share.
- llms.txt has no confirmed ranking or citation effect; it stays low priority.

## Output format

Crawler table, citation table, share of voice; keys `ai-answer-crawlers-blocked:site`,
`ai-training-crawler-policy:site`, `robots-ai-crawlers:site`, `llms-txt:site`,
`ai-overview-not-cited:<kw>:<market>`, `ai-prompt-gap:<prompt>`.

## Quality checks

Never claim an engine cites the site without a recorded observation; answers change daily.

## Error handling

robots.txt unreachable: crawler access is not judged (no recheck claimed).

## Website-specific configuration

`ai_visibility`, `key_pages`, `brand_name`.

## How findings are stored

Ledger findings, `insights/ai-visibility-<date>.json`, `insights/ai-answers.json`, the
`ai-visibility` execution.

## Improving this skill

Through proposals (`seo-learning-and-memory`) and `/seo-update-skills`.
