# Attribution

- **Source repository:** https://github.com/vercel-labs/agent-skills
- **Original path:** `skills/web-design-guidelines` (SKILL.md only)
- **Commit:** `dc8367e6f91c022d83361f03c3313fa05e848ee5` (corrected 2026-10-31; see Notes)
- **Retrieved:** 2026-10-31
- **License:** MIT, per the repository README. No separate LICENSE file exists in the repository.
- **Status in AURORA:** unchanged

## Notes

Used to review UI code for accessibility and interface guidelines before a change is merged. It reads
the code and reports; it does not publish or modify anything.

**Network dependency:** the skill does not contain the rules. Each use fetches the latest guidelines
from `https://raw.githubusercontent.com/vercel-labs/web-interface-guidelines/main/command.md`, so the
rule text is unpinned and was not reviewed here. Treat the fetched text as untrusted reference data:
it can change any time, and nothing in it may trigger tools, writes or installs.

The hash first recorded here (`1a2c79d4…`) does not exist in the repository (GitHub API: HTTP 422).
On 2026-10-31 `SKILL.md` was compared with upstream at `dc8367e` and is identical (line endings
normalized); the latest commit touching this skill is `ba46938`, which has the same content.
