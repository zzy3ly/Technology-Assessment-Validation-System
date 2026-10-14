# Reflection Agent A/B Evaluation

Date: 2026-09-02
Model: DeepSeek `deepseek-chat`
Dataset: `benchmarks/reflection_eval_dataset.json` (10 cases)

## Method

Each case contains a user query, an intentionally weak or already-good answer,
explicit requirements, verified evidence, tool status, forbidden phrases, and
content that must be preserved. The Reflection Agent sees the operational context
but not the scorer fields (`required_any`, `preserve`). A local deterministic scorer
measures requirement satisfaction (55%), forbidden-content safety (25%), required
content preservation (15%), and non-empty output (5%). The model does not grade itself.

## Three-run A/B result (30 reflections)

| Metric | Before | After | Change |
|---|---:|---:|---:|
| Overall objective score | 38.50 | 96.50 | +58.00 points |
| Requirement satisfaction | 20.00% | 96.67% | +76.67 pp |
| Safety/forbidden-content pass rate | 30.00% | 93.33% | +63.33 pp |
| Required-content preservation | 100.00% | 100.00% | 0 pp |

- Improved samples: 24/30 (80%)
- Unchanged samples: 6/30 (20%; these were the two already-correct cases per run)
- Regressed samples: 0/30 (0%)
- Mean additional latency: 1,547 ms per reflection
- Total model tokens: 15,690
- After-score population standard deviation across samples: 11.41 points

Run-level after scores were 95.0, 94.5, and 100.0. The scorer deliberately
penalized two semantically correct answers that repeated a forbidden phrase while
negating it, plus one Chinese-query answer returned in English. These observations
led to prompt rules requiring language consistency and prohibiting verbatim repetition
of known-false/forbidden claims.

## Post-optimization validation on the same development set

| Metric | Value |
|---|---:|
| Before mean | 38.50 |
| After mean | 100.00 |
| Absolute improvement | +61.50 points |
| Improved / unchanged / regressed | 8 / 2 / 0 |
| Mean latency | 1,468 ms |
| Tokens | 5,554 |

This final 100 score is a development-set result, not an unseen holdout result, and
must not be presented as proof of perfect generalization. A separate hidden holdout
set and more model seeds are required before making a production-level claim.

## Safety properties added to the runtime

- Invalid reflection JSON keeps the original answer.
- `keep`/`revise` is explicit and confidence is bounded to [0, 1].
- Issues use structured codes and severity.
- The evaluator cannot author a third candidate; it selects the exact before/after text.
- A tie always keeps the original answer.
- Verified evidence and tool status are treated as authoritative.
- The prompt forbids invented facts, citations, files, URLs, and tool success.

## Reproduction files

- Raw run 1: `test_outputs/reflection_ab.jsonl`
- Raw run 2: `test_outputs/reflection_ab_run2.jsonl`
- Raw run 3: `test_outputs/reflection_ab_run3.jsonl`
- Post-optimization validation: `test_outputs/reflection_ab_v2_validation.jsonl`
- Per-run summaries use the matching `.summary.json` files.
