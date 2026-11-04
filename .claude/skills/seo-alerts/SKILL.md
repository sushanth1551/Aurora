---
name: seo-alerts
description: "SEO Intelligence early warning for a configured website (weekly Windows task 'AURORA SEO Alerts' per site, or on demand): robust anomaly detection on Search Console clicks and impressions (site and page level, last 7 days vs the 8 weeks before using median and MAD), query position drops of 3+ places, and URL Inspection of key pages (not indexed, FAIL verdict, Google chose another canonical). Writes a PDF only when something needs attention. Use for /seo-alerts, 'did traffic drop', 'is anything broken', 'are my key pages indexed'."
user-invocable: true
argument-hint: "[site-id] [--pdf]"
metadata:
  author: AURORA
  version: "1.0.0"
  category: seo-intelligence
---

# /seo-alerts

## When to activate

"Did traffic drop", "anything wrong this week", "are my main pages indexed", after a deploy or a
Google update, or when the weekly scheduled run produced a PDF.

## Required inputs

Website id; Search Console property in the config; PostgreSQL running.

## Tools and integrations

`.venv/Scripts/python scripts/seo_insights.py alerts <site> [--pdf] [--no-inspect]
[--execution <id>]`. Read-only Search Analytics (date, date x page, date x query for 63 days) and
URL Inspection (up to 10 key pages). The scheduler runs `scripts/seo-alerts.ps1`.

## Execution steps

1. Run the command (or read `insights/alerts-latest.json` for the scheduled run).
2. For each alert, in priority order, say what moved, by how much, against which baseline, and
   the first thing to check (deploy date, Google update, competitor, indexing, AI Overview).
3. Index-status alerts: after the fix is live, hand reindex requests to the `seo-reindex-agent`.
4. No alerts: say so in one line with the window checked.

## Analysis methodology

- Weekly sums over 9 weeks; the latest week vs the median of the 8 before. An alert needs a
  robust z <= -3 (configurable `alerts.robust_z`), a drop >= 25% (`alerts.min_drop_pct`) and enough
  baseline volume (`alerts.min_weekly_clicks`). A flat baseline (MAD 0) uses the percentage alone.
- Query position: impression-weighted position 3+ places worse, with >= 100 baseline impressions.
- URL Inspection: indexed states pass; anything else, a FAIL verdict or a Google-chosen canonical
  different from the declared one is an alert.

## Output format

Short list: priority, what, numbers, first check, owner action. Keys: `traffic-drop:site`,
`impressions-drop:site`, `page-clicks-drop:<path>`, `query-position-drop:<query>`,
`index-status:<path>`.

## Quality checks

Alerts are observations, not diagnoses; name the baseline; never claim a cause without evidence.

## Error handling

Database down: the scheduled script starts Docker Desktop and the postgres container; if it still
fails the run is `blocked` and says why.

## Website-specific configuration

`alerts` section of the site config (run time, interval, thresholds, inspection on/off) and
`key_pages`. Re-register the task after changing the schedule:
`powershell -ExecutionPolicy Bypass -File scripts\register-seo-alerts.ps1`.

## How findings are stored

Ledger findings (keys above), `insights/alerts-<date>.json`, the `alerts` execution and report,
and a PDF in the scheduler's PDF folder when alerts exist.

## Improving this skill

Through proposals (`seo-learning-and-memory`) and `/seo-update-skills`.
