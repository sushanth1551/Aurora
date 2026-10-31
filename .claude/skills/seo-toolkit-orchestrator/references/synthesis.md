# Synthesis and conflict resolution

Load this when combining outputs from several skills.

## Why this exists

A conformant host already discovers skills and picks one. The value added here is the
part the host does not do: ordering work, reconciling outputs that disagree, and
producing one prioritized answer instead of several parallel reports.

Do not re-decide which single skill applies. That is already handled.

## Ordering

Dependencies, not importance, set the order:

1. **Establish scope and capabilities** before anything. What evidence exists decides
   what can be concluded, and therefore which skills are worth running.
2. **Evidence collection before interpretation.** A content plan built before knowing
   the pages are non-indexable is wasted work.
3. **Eligibility before optimization.** If pages cannot be reached, crawled or
   indexed, everything downstream is conditional — say so explicitly.
4. **Diagnosis before prioritization.** Prioritizing findings that do not exist yet
   produces invented findings.
5. **Anything irreversible last**, and only after its evidence is in.

When a step's input is missing, say what is blocked rather than substituting a guess.

## Resolving contradictions

Contradictions between specialists are information, not noise. Never average them and
never silently pick one.

| Situation | Resolution |
| --- | --- |
| One is `observed`, the other `inferred` | Direct observation wins; keep the inference as a hypothesis |
| Different scopes — one page versus site-wide | Both can be true. State the scope of each |
| Different observation dates | Prefer the more recent; say the older may be stale |
| Different sources with different capabilities | Prefer the source that can actually prove the claim |
| Genuinely irreconcilable | **Present both, say why they conflict, and name the check that would settle it** |

An unresolved conflict clearly stated is a better deliverable than a confident
answer that buried it.

## Common cross-domain conflicts

- Content wants a new page; cannibalization analysis says consolidate. Resolve on
  whether a distinct query family and a distinct job exist.
- Programmatic proposes scale; technical shows crawl waste already. Resolve in favour
  of fixing the existing waste first, then a bounded pilot.
- Keyword research proposes targets; performance data shows the site already ranks
  and loses clicks. Resolve towards the snippet and page, not new pages.
- Migration wants speed; technical wants gates. Resolve by naming which gates are
  reversible and which are not.

## Final prioritization

Rank across domains, not within each. Anything blocking eligibility outranks
everything else, then reversible high-confidence work, then work that reduces
uncertainty, then the rest. Preserve each finding's evidence state through the
synthesis — do not let a confident recommendation absorb an uncertain input.

Close with what was not covered, what evidence is missing, and the next check.

## When the host cannot load another skill

Say which analysis is missing and name it. Then give a bounded plan using what you do
have. Never imply that a skill ran when it did not, and never reproduce a specialist's
detailed instructions from memory in its place.
