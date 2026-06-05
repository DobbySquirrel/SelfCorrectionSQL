# Search Failure Breakdown (O2)

Generated: 2026-06-03T14:10:30

Population: **8** questions (43 regressions with recall ✗).

## 1. Bucket counts

| Bucket | Count | % | Fix | Est. save |
|---|---:|---:|---|---:|
| S1 | 0 | 0.0% | `mcts_workflow.py` | 2 |
| S2 | 3 | 37.5% | `cte_generator.py` | 3 |
| S3 | 0 | 0.0% | `prompts / schema linker` | 2 |
| S4 | 0 | 0.0% | `ddl preprocessing` | 2 |
| S5 | 5 | 62.5% | `sql_result_processor.py` | 2 |
| S6 | 0 | 0.0% | `—` | 0 |

**Union fixable (excl. S6)**: 8 / 8 qids

## 2. Per-qid summary

| qid | db | bucket | reason | gold_cte | max_depth | d1_clusters |
|-----|-----|--------|--------|----------|----------|-------------|
| 32 | california_schools | S5 | high_reward_no_correct_path | 0 | 8 | 2.8 |
| 263 | toxicology | S5 | high_reward_no_correct_path | 0 | 8 | 1.6 |
| 1227 | thrombosis_prediction | S5 | high_reward_no_correct_path | 0 | 8 | 1.7 |
| 1238 | thrombosis_prediction | S5 | high_reward_no_correct_path | 0 | 8 | 1.5 |
| 1357 | student_club | S2 | low_d1_diversity | 0 | 8 | 1.1 |
| 1486 | debit_card_specializing | S5 | high_reward_no_correct_path | 0 | 8 | 2.0 |
| 1498 | debit_card_specializing | S2 | low_d1_diversity | 0 | 8 | 1.2 |
| 1505 | debit_card_specializing | S2 | low_d1_diversity | 0 | 4 | 1.0 |

## 3. debit_card deep dive (1498 + non-regress wrong)

### qid=1472 (debit_card_specializing)
- In 8 search failures: **no** (other failure mode)

### qid=1482 (debit_card_specializing)
- In 8 search failures: **no** (other failure mode)

### qid=1498 (debit_card_specializing)
- In 8 search failures: **yes** — bucket **S2**: low_d1_diversity

### qid=1529 (debit_card_specializing)
- In 8 search failures: **no** (other failure mode)

### qid=1531 (debit_card_specializing)
- In 8 search failures: **no** (other failure mode)

## 4. Fix directions (ROI)

| Rank | Bucket | Est. q | Risk |
|---:|---|---:|---|
| 1 | S5 | 5 | see FIX map |
| 2 | S2 | 3 | see FIX map |
| 3 | S1 | 0 | see FIX map |
| 4 | S3 | 0 | see FIX map |
| 5 | S4 | 0 | see FIX map |

## 5. Not framework-fixable (S6 + residual): **0** qids

Treat as model-upgrade / limitation section; do not count in framework patch ROI.

