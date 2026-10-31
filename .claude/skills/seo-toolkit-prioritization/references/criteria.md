# Prioritization criteria

Load this when ordering a set of findings.

## The ordering principle

Rank by **expected value under uncertainty**, not by severity labels. Four inputs:
potential impact, confidence that the impact is real, effort and dependency, and
risk and reversibility.

A high-impact, low-confidence, irreversible item does not go first. The item that goes
first is usually the one that **removes uncertainty cheaply** — because it makes
everything after it better decided.

## Tiers that survive contact with a team

1. **Blockers** — something is broken now, losing value now, or preventing other work.
2. **Cheap certainties** — high confidence, low effort, reversible. Do them regardless
   of impact ranking; deliberating costs more than doing.
3. **Uncertainty reducers** — measurements and checks that decide what comes next.
4. **High-leverage work** — real impact, real effort, acceptable confidence.
5. **Experiments** — plausible, unproven, bounded and reversible.
6. **Deferred** — blocked on evidence, a dependency or a decision. Say what unblocks it.

## Scoring honestly

If a numeric score is requested, show the rubric, show the inputs, and label the
result a planning judgment. **Never let missing data enter a formula as a number** —
that laundering of an unknown into a figure is the failure this skill exists to
prevent. Where an input is unknown, say so and let the tier carry the decision.

Ordinal labels with stated reasoning beat invented precision. "High impact, low
confidence, because the affected template covers most of the catalogue but no
performance data was supplied" is more useful than "7.4".

## Effort and dependency

Effort is not yours to assert. If the team has not supplied it, mark it
`not_available`, give the shape of the work — one change or many, one team or several,
data needed or not — and ask.

Dependencies change order more than impact does. An item that unblocks three others
outranks a slightly higher-impact isolated item. State the chain.

## Risk and reversibility

Weight irreversibility heavily. Consolidations, redirects, deletions and mass
directive changes are expensive to undo; a title change is not. Two items with the
same expected impact are not equal if one can be reverted this afternoon.

## What the output must carry

Each item: the evidence behind it, the finding, potential impact, confidence, effort
or `not_available`, dependency, owner, the recommendation, and how it will be
validated. Then the trade-offs — what is deliberately not being done first, and why.

Use *potential* impact throughout. Never promise traffic, rankings or revenue.
