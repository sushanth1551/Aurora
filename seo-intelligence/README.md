# SEO Intelligence knowledge base

Structured memory of the SEO Intelligence agent (`docs/seo-intelligence.md`). Written only through
`scripts/seo_intelligence.py` or the AURORA services; validated by Pydantic models in
`backend/app/seo/intelligence/models.py`. Check it with `python scripts/seo_intelligence.py validate`.

```
config/sites/<site>.json             website configuration (no secrets)
skills/skill-registry.json           which skill plays which role in which mode (reviewed)
skills/proposals/<id>.json           proposed skill changes (pending/approved/applied/...)
skills/CHANGELOG.md                  applied skill changes (created on first apply)
sites/<site>/website-profile.json    latest discovered profile
sites/<site>/findings.json           finding ledger: every issue, status and history
sites/<site>/implemented-changes.json
sites/<site>/rejected-recommendations.json
sites/<site>/metrics/performance-history.json   sourced metrics only
sites/<site>/research/{keyword,competitor,content-strategy}/<id>.json
sites/<site>/executions/execution-history.json  index of executions
sites/<site>/executions/<execution_id>.json     full execution record
sites/<site>/reports/<execution_id>.md, latest-report.md
sites/<site>/changes/commits.json    SEO commits detected in the site repository (monitor)
sites/<site>/checklist.json          change checklist: deploy, live title, reindex, ranking checks
sites/<site>/learning/change-effects.json   ranking effect by change type, net of site trend
skills/skill-hashes.json             baseline for detecting direct skill edits
```

Rules: no credentials, no raw page dumps or provider payloads (use `data/`), no invented metrics,
and no hand edits that bypass validation. Each website's folder holds only that website's data.
