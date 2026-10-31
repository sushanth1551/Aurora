# Module 4 evaluation results

Generated 2026-10-31 13:09 UTC by `scripts/evaluate_module4.py` (mode: **offline**).
Provider: mock (evidence-citing fake with planted faults). Research data: Acme fixture (tests/intel_support.py).

All columns are **automated structural/lexical checks**. None of them measures factual
correctness; citation coverage says every factual statement cites stored evidence, not
that the statement is true. Factual correctness and readability need the manual review
section below.

## Automated checks per objective

| id | status | quality | evidence_items | evidence_trimmed | citation_coverage | evidence_coverage | invalid_citations | unsupported | contradictions_disclosed | undisclosed_contradictions | relevance | completeness | structural | expected_terms_found | sources | llm_calls | tokens | estimated_cost | markdown_chars | invented_id_absent | figure_flagged | ranking_downgraded |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Q01 | requires_review | requires_review | 27 | False | 1.0 | 0.556 | 0 | 4 | 0 | 0 | 0.719 | 1.0 | True | 1/2 | 15 | 5 | 750 | None | 6497 | True | True | True |
| Q02 | requires_review | requires_review | 27 | False | 1.0 | 0.556 | 0 | 4 | 0 | 0 | 0.719 | 1.0 | True | 3/3 | 15 | 5 | 750 | None | 6557 | True | True | True |
| Q03 | requires_review | requires_review | 27 | False | 1.0 | 0.556 | 0 | 4 | 0 | 0 | 0.719 | 1.0 | True | 1/1 | 15 | 5 | 750 | None | 6431 | True | True | True |
| Q04 | requires_review | requires_review | 27 | False | 1.0 | 0.519 | 0 | 4 | 0 | 0 | 0.719 | 1.0 | True | 2/2 | 14 | 5 | 750 | None | 6416 | True | True | True |
| Q05 | requires_review | requires_review | 27 | False | 1.0 | 0.593 | 0 | 3 | 0 | 0 | 0.743 | 1.0 | True | 2/2 | 16 | 5 | 750 | None | 6512 | True | True | True |
| Q06 | requires_review | requires_review | 27 | False | 1.0 | 0.556 | 0 | 4 | 0 | 0 | 0.719 | 1.0 | True | 2/2 | 15 | 5 | 750 | None | 6476 | True | True | True |
| Q07 | requires_review | requires_review | 27 | False | 1.0 | 0.519 | 0 | 3 | 0 | 0 | 0.738 | 1.0 | True | 2/2 | 14 | 5 | 750 | None | 6294 | True | True | True |
| Q08 | requires_review | requires_review | 27 | False | 1.0 | 0.593 | 0 | 3 | 0 | 0 | 0.743 | 1.0 | True | 0/2 | 16 | 5 | 750 | None | 6494 | True | True | True |
| Q09 | requires_review | requires_review | 27 | False | 1.0 | 0.593 | 0 | 4 | 0 | 0 | 0.719 | 1.0 | True | 0/1 | 16 | 5 | 750 | None | 6590 | True | True | True |
| Q10 | requires_review | requires_review | 27 | False | 1.0 | 0.556 | 0 | 4 | 0 | 0 | 0.719 | 1.0 | True | 0/2 | 15 | 5 | 750 | None | 6551 | True | True | True |
| Q11 | requires_review | requires_review | 27 | False | 1.0 | 0.519 | 0 | 4 | 0 | 0 | 0.719 | 1.0 | True | 2/2 | 14 | 5 | 750 | None | 6437 | True | True | True |
| Q12 | requires_review | requires_review | 27 | False | 1.0 | 0.556 | 0 | 4 | 0 | 0 | 0.719 | 1.0 | True | 3/3 | 15 | 5 | 750 | None | 6575 | True | True | True |

Objectives:

- **Q01** What products does Acme offer and what do they do?
- **Q02** What pricing plans does Acme publish, with prices and billing periods?
- **Q03** Where is Acme headquartered?
- **Q04** When was Acme founded and how large is the company?
- **Q05** Which integrations does Acme Flow document?
- **Q06** Which technologies does Acme mention using?
- **Q07** Who are Acme's target customers?
- **Q08** What features does Acme Flow provide?
- **Q09** What use cases does Acme describe for its product?
- **Q10** Does Acme offer a free trial, and how is enterprise pricing handled?
- **Q11** Compare Acme's Starter and Pro plans on price and billing.
- **Q12** Summarise Acme as a company: what it does, where it is based, and its scale.

All objectives run as `company_intelligence`, which includes only the company
comparison table by design; product-level objectives (features, use cases, trial)
therefore score low on `expected_terms_found`. That column is informational and
is not a gate.

## Planted-fault detection (offline mode only)

The fake provider deliberately cites an invented evidence id, states an unsupported
figure ("9000 customers across 40 countries") and makes a ranking claim. Expected:
the invented id never reaches the report, the figure is flagged `unsupported_figure`,
and the ranking is downgraded to a hypothesis. See the last three columns above.

## Contradiction handling (synthetic two-company context)

- Conflicts in context: 1
- Report that discloses the conflict: undisclosed=0, status=failed
- Report that hides the conflict: undisclosed=1, status=failed
- Conflicting value kept out of comparison tables: True

## Manual review (to be completed by a person)

| id | factual correctness | numerical accuracy | readability | notes |
| --- | --- | --- | --- | --- |
| Q01 | not performed: mock output is not real analysis | not performed: mock output is not real analysis | not performed: mock output is not real analysis | |
| Q02 | not performed: mock output is not real analysis | not performed: mock output is not real analysis | not performed: mock output is not real analysis | |
| Q03 | not performed: mock output is not real analysis | not performed: mock output is not real analysis | not performed: mock output is not real analysis | |
| Q04 | not performed: mock output is not real analysis | not performed: mock output is not real analysis | not performed: mock output is not real analysis | |
| Q05 | not performed: mock output is not real analysis | not performed: mock output is not real analysis | not performed: mock output is not real analysis | |
| Q06 | not performed: mock output is not real analysis | not performed: mock output is not real analysis | not performed: mock output is not real analysis | |
| Q07 | not performed: mock output is not real analysis | not performed: mock output is not real analysis | not performed: mock output is not real analysis | |
| Q08 | not performed: mock output is not real analysis | not performed: mock output is not real analysis | not performed: mock output is not real analysis | |
| Q09 | not performed: mock output is not real analysis | not performed: mock output is not real analysis | not performed: mock output is not real analysis | |
| Q10 | not performed: mock output is not real analysis | not performed: mock output is not real analysis | not performed: mock output is not real analysis | |
| Q11 | not performed: mock output is not real analysis | not performed: mock output is not real analysis | not performed: mock output is not real analysis | |
| Q12 | not performed: mock output is not real analysis | not performed: mock output is not real analysis | not performed: mock output is not real analysis | |

Automated and manual results are kept separate on purpose: a report can pass every
automated gate and still be wrong, and a report flagged `requires_review` can be right.
