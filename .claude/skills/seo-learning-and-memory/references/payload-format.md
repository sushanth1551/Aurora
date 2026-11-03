# Execution payload format (`record --file`)

Validated by `ExecutionPayload` in `backend/app/seo/intelligence/models.py`. Unknown fields are
rejected. All lists are optional; a payload may carry one step's results or a whole run.

```json
{
  "summary": "One or two sentences on what this run did.",
  "pages_analysed": ["https://example.com/", "https://example.com/blog/post-a"],
  "reports_processed": ["C:/path/to/report.pdf (tool, date)"],
  "skills_used": [
    {
      "name": "website-seo-discovery",
      "reason": "Why it was selected for this run",
      "analysed": "What it looked at",
      "actions": ["what it did", "..."],
      "output": "What it produced"
    }
  ],
  "tools_used": [
    {"name": "seo-data audit_page", "kind": "mcp", "status": "success", "calls": 9, "note": ""},
    {"name": "seo-data pagespeed", "kind": "mcp", "status": "unavailable", "calls": 1,
     "note": "daily quota exhausted"}
  ],
  "findings": [
    {
      "key": "meta-description-length:/mentorship",
      "issue": "Meta description is 178 characters",
      "category": "on-page/metadata",
      "priority": "medium",
      "evidence": [
        {"source": "seo-data audit_page", "observation": "meta description length 178",
         "url": "https://example.com/mentorship"}
      ],
      "evidence_state": "observed",
      "affected_urls": ["https://example.com/mentorship"],
      "seo_impact": "Google truncates the snippet, hiding the call to action.",
      "recommended_action": "Rewrite to 150-160 characters keeping the main query first.",
      "expected_outcome": "The full description can show in results; CTR may improve.",
      "verification_method": "Re-run audit_page; then compare CTR over 28 days in Search Console.",
      "status": "open",
      "skill": "seo-toolkit-page-audit"
    }
  ],
  "checked": {"categories": ["on-page/metadata"], "keys": []},
  "changes": [
    {"kind": "knowledge", "description": "Saved keyword snapshot", "path": "seo-intelligence/sites/x/research/keyword/....json"}
  ],
  "blockers": ["PageSpeed quota exhausted: Core Web Vitals not measured"],
  "learning": {
    "learned": [], "reused": [], "patterns": [], "skill_proposals": [], "needs_approval": []
  },
  "notes": []
}
```

## Field rules

- `priority`: `critical` (blocks indexing, conversions or causes legal/trust risk now), `high`,
  `medium`, `low`. Use the prioritisation rubric, not gut feeling.
- `evidence_state`: `observed` (seen in this run), `reported` (taken from a third-party or older
  report, not re-observed), `inferred` (interpretation of evidence), `not_available`.
- `status`: `open`, `in_progress`, `implemented` (changed in code, not yet verified live),
  `verified`, `resolved`, `deferred`. Use the `reject` command for rejections.
- `tools_used[].kind`: `mcp`, `api`, `cli`, `aurora`, `file`, `git`, `manual`, `other`.
  `status`: `success`, `partial`, `unavailable`, `failed`, `skipped`.
- `changes[].kind`: `file_created`, `file_modified`, `seo_change` (a change to the website; also
  copied to `implemented-changes.json`), `knowledge`, `skill`.

## Finding keys

Stable, lowercase, no spaces: `<check>:<scope>`. Scope is a path (`/mentorship`), a slug, or
`site` for site-wide issues. Examples: `title-too-long:/blog/oet-exam-fees`,
`soft-404:site`, `reviewer-schema-missing-sameas:site`, `cannibalization:step-2-ck-score`.
Imported AURORA technical-audit findings use `tech:<check_id>`. Reuse existing keys from
`context`; one issue keeps one key forever.

## Metrics (`metrics --file`)

```json
[{"metric": "clicks", "value": 2007, "unit": "clicks", "source": "Google Search Console (AURORA GSC API), country=IND",
  "period": "2026-08-01..2026-10-30", "scope": "site:IN"}]
```

## Research snapshot (`research --file`)

```json
{"id": "20261103-keywords", "kind": "keyword", "sources": ["Google autocomplete via seo-data"],
 "summary": "...", "items": [{"keyword": "...", "intent": "informational", "owner_page": "/blog/..."}],
 "limitations": ["No search volume data"]}
```
