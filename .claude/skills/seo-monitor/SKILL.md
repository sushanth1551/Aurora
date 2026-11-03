---
name: seo-monitor
description: "SEO Intelligence monitor for a configured website (runs every 14 days by the Windows scheduler, or on demand with /seo-monitor): detects the owner's SEO changes in the site repository, keeps a change checklist (deployed, live title, reindex, ranking checks 14/28/56 days after go-live), reports blog rankings from Search Console, flags ranking drops, learns which kinds of change helped on this site, and detects direct skill edits. Use for /seo-monitor, 'how are my blogs ranking', 'did my changes work', or 'what is left on the checklist'."
user-invocable: true
argument-hint: "[site-id]"
metadata:
  author: AURORA
  version: "1.0.0"
  category: seo-intelligence
---

# /seo-monitor

## When to activate

`/seo-monitor`, "how are the blogs ranking", "did my changes work", "what's on my SEO checklist",
or after the scheduled run finished and the user wants it explained.

## Required inputs

- Website id (default: the only configured site).
- PostgreSQL running (holds the sealed Search Console token); otherwise rankings are skipped
  and the run is `partial`.

## Tools and integrations

- `scripts/seo_monitor.py <site>`: the deterministic run (git read-only, live title fetches
  through `UrlPolicy`, Search Console date x page rows, no paid APIs). The scheduler runs the
  same script through `scripts/seo-monitor.ps1`.
- `scripts/seo_intelligence.py` for history, findings and proposals.

## Execution steps

1. If the user asked for a fresh run, execute
   `.venv/Scripts/python scripts/seo_monitor.py <site>`; otherwise read the newest
   `seo-intelligence/sites/<site>/reports/*-monitor.md`.
2. Summarise for the user, in this order:
   - blog rankings: totals, biggest movers, new `ranking-drop:*` findings;
   - their changes since the last run and whether each is live;
   - "Needs you": deploys, reindex requests (only for live changes), findings to re-verify;
   - ranking checks completed and the learning table (`learning/change-effects.json`).
3. Candidate rules (a change type that improved or declined in >= 70% of >= 5 measured
   pages): explain the evidence and its limits, then ask whether to adopt it. If the user agrees:
   - a website-specific rule goes into the site config `rules.editorial` (show the diff first);
   - a generic method change becomes a skill proposal (`seo-learning-and-memory`, then
     `/seo-update-skills`).
   Never adopt a rule without the user's yes.
4. When the user says they requested indexing or deployed something, record it: reindex rows go
   in the site's reindex tracker (their file; edit only with permission) and decisions through
   `scripts/seo_intelligence.py finding ...`.

## Analysis methodology

- Ranking verdicts compare N days after go-live with N days before, per page, with Search
  Console's impression-weighted position. Seasonality, Google updates and other edits also move
  rankings; present verdicts as observations.
- Go-live dates: commit date for changes already on the production branch at the first sync,
  the run that first saw them on production, or a confirmed live title (stated per commit).
- Positions are averages across all queries of a page; check the query mix before acting on a drop.

## Output format

Short chat summary (rankings, changes, needs-you list, learning, candidate rules) plus the report
path.

## Quality checks

No forecasts or promised rankings; every number names its window; candidate rules list sample
sizes.

## Error handling

Database not running: start it (`docker compose up -d postgres`) with the user's go-ahead, or
report rankings as not measured. Git unavailable: changes not tracked, say so.

## Website-specific configuration

Repository path, tracked SEO paths, production branch, Search Console property and base URL
come from the website config.

## How findings are stored

`changes/commits.json`, `checklist.json`, `learning/change-effects.json`, the `monitor`
execution, ranking-drop findings in the ledger, and the report.

## Improving this skill

Through proposals (`seo-learning-and-memory`) and `/seo-update-skills`.
