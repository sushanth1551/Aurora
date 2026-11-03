---
name: seo-learning-and-memory
description: "Load and store SEO knowledge for a configured website: open findings, decisions, execution history, metrics and research snapshots; compare a run with previous runs; propose (never silently apply) skill improvements. Use at the start and end of every /seo-* execution, or when asked what was found, changed or learned before. Do NOT use to analyse a website itself."
metadata:
  author: AURORA
  version: "1.0.0"
  category: seo-intelligence
---

# SEO learning and memory

The SEO Intelligence agent's memory is structured JSON in `seo-intelligence/` (repository root),
written only through `scripts/seo_intelligence.py`. This skill says how to read it before work
and how to write it afterwards. Storing evidence is not the same as learning a new rule: rules
change only through reviewed skill proposals.

## When to activate

- First step of `/seo-audit`, `/seo-competitor-analysis`, `/seo-content-optimization`,
  `/seo-performance-review` and `/seo-update-skills` (load), and their last step (store).
- When the user asks what was found, implemented, rejected or learned for a website.

## Required inputs

- A website id configured in `seo-intelligence/config/sites/<id>.json` (list them with `sites`).
- For storing: the running execution id from `start`, and the results of the analysis steps.

## Tools and integrations

- The CLI, run from the AURORA repository root with the project virtualenv:
  `.venv/Scripts/python scripts/seo_intelligence.py <command>` on Windows, or
  `.venv/bin/python ...` elsewhere. Commands are listed in the script's docstring.
- No network access. The CLI validates every record with Pydantic and refuses invalid ones.

## Execution: load (start of a run)

1. `sites`: confirm the website id. If it is missing, stop and ask the user to add a config
   (copy `seo-intelligence/config/sites/nextsteps.json` as a template; see `docs/seo-intelligence.md`).
2. `context <site>`: read the website config, active findings, rejected recommendations, the last
   five executions, recent metrics and research snapshots.
3. `plan <site> --mode <mode>`: the ordered skill steps, each with `availability`
   (`available`, `missing_skill`, `missing_setting`, `runtime_check`). For `runtime_check` steps,
   confirm the MCP server's tools are actually present in this session before relying on them.
4. `start <site> --mode <mode>`: creates the running execution; keep the returned id.
5. Note what you reuse: rejected recommendations must not be re-proposed unless new evidence
   contradicts the stated reason. Say so explicitly when that happens.

## Execution: store (end of a run)

1. Write one payload file per batch in the session scratchpad (never in the repository) using
   [the payload format](references/payload-format.md), then
   `record <site> <execution_id> --file <payload.json>`. Several `record` calls may be made.
2. Fill `checked` honestly: list the finding categories (and keys) this run re-examined. A previous
   finding becomes "resolved" only if its key or category was rechecked and it was not observed;
   otherwise it stays "not rechecked". Never list a category you did not check.
3. `finish <site> <execution_id> --status <completed|partial|blocked|failed>`:
   - `completed` only if every non-optional step ran with the data it needed (no blockers).
   - `partial` if some steps ran without their data; `blocked` if the core data was missing.
   - The command merges findings into the ledger, compares with history and writes
     `sites/<site>/reports/<execution_id>.md` and `latest-report.md`.
4. Separate records when relevant: `metrics` (sourced numbers only), `research` (keyword,
   competitor or content-strategy snapshots), `profile` (the discovered website profile).
5. Decisions made by the user during the run:
   - `finding <site> <key> --status implemented|verified|deferred|in_progress --note ... --reference <commit or file>`
   - `reject <site> <key> --reason "..." --by "<name>"` (the reason is kept and reused).

## Analysis methodology (comparison)

- Findings are matched across runs by their stable `key` (see the payload format for key rules).
  Reuse the key from `context` when re-observing a known issue; never mint a new key for it.
- New, recurring, reopened, resolved and not-rechecked findings are computed by the service; do
  not compute them by hand.
- "Implemented but still observed" means the change is not deployed or did not work: report it,
  check the deployment state, and do not mark it verified.
- An implemented change is judged effective only from evidence gathered after it was deployed
  (for example a later Search Console window). Record such judgements with `finding ... --status verified`.

## Proposing skill improvements

1. Look for a pattern across executions (`history <site>`, the ledger's `seen_in` lists): a
   recurring problem the methodology misses, a check that keeps producing rejected advice, or a
   threshold that proved wrong.
2. Write a proposal JSON (`skill`, `change` {`kind`: `append_section`|`replace_text`, `text`,
   `section_title` or `old_text`}, `rationale`, `evidence`, `evidence_executions`) and run
   `propose --file <proposal.json>`. Only AURORA-owned skills can be changed; third-party skills
   are reused as they are.
3. List the proposal in the execution's `learning.skill_proposals` and `learning.needs_approval`.
   Never apply it in the same run; `/seo-update-skills` handles approval.

## Output format

Report back: execution id, final status, the comparison counts, the report path, and any
records written (metrics, research, profile, decisions, proposals).

## Quality checks

- Every finding has at least one evidence item naming its source; reported (third-party) findings
  use `evidence_state: "reported"` until re-observed.
- No invented numbers: metrics carry `source` and `period`; unknown values are left out.
- Expected outcomes never promise rankings or traffic (the CLI rejects "guarantee").
- The tools list includes unavailable and failed integrations, not only successes.

## Error handling

- Exit code 2 prints `{"error", "message"}`: fix the payload and retry; never edit the JSON files
  by hand to get around validation.
- "still running" on `start`: finish the old execution as `failed` or `partial` with a note first.
- An invalid knowledge file (exit 1 from `validate`): report it to the user; do not overwrite it.

## Website-specific configuration

Website facts, editorial rules, competitors and approval requirements live in the website config,
not in this skill. Read them from `context`.

## How findings are stored

See the layout in `seo-intelligence/README.md`. The finding ledger
(`sites/<site>/findings.json`) keeps every finding ever seen with its status history; executions
keep what each run observed; reports are regenerated deterministically from both.

## Improving this skill

Propose changes with the process above. Typical candidates: better default checked-scope
categories, payload fields that runs keep needing, comparison rules that misclassified a finding.
