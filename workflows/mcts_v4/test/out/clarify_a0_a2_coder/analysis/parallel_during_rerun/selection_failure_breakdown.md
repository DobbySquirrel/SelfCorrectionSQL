# Selection Failure Breakdown (O1)

Generated: 2026-06-03T14:10:24

Population: **35** questions (baseline Hit@1 ✓, Final Hit@1 ✗, recall ✓, excl. ef2).

## 1. Bucket counts

| Bucket | Count | % | Fix target | Est. fixable |
|---|---:|---:|---|---:|
| B1 | 10 | 28.6% | `workflows/mcts_v4/utils/sql_result_processor.py` | 10 |
| B2 | 0 | 0.0% | `workflows/mcts_v4/utils/sql_selector.py` | 0 |
| B3 | 12 | 34.3% | `workflows/mcts_v4/utils/sql_result_processor.py` | 12 |
| B4 | 0 | 0.0% | `workflows/mcts_v4/mcts_workflow.py` | 0 |
| B5 | 7 | 20.0% | `workflows/mcts_v4/utils/mcts_helpers.py` | 7 |
| B6 | 6 | 17.1% | `—` | 0 |

### Overlap (if fixes applied independently)

| Combination | Est. qids saved |
|---|---:|
| B1 | 10 |
| B2 | 0 |
| B3 | 12 |
| B1+B2 | 10 |
| B1+B2+B5 | 17 |
| All B1–B5 (union) | 29 |
| B6 (manual only) | 6 |

## 2. Representative examples (1 per bucket)

### B1 — qid=232 (toxicology)

- Reason: gold_cluster_lower_bucket_count_in_winning_rollout
- max_reward=1.0, final_sig=`7d57158c2a15f957…`, gold_sigs=1

| sig (prefix) | variants | correct | max_bucket | avg_reward | rollouts |
|---|---:|---:|---:|---:|---|
| `1de94db23553…` **GOLD** | 72 | 72 | 15 | 0.960 | [1, 3, 4, 6, 8] |
| `7d57158c2a15…` **FINAL** | 44 | 0 | 15 | 0.978 | [2, 5, 7] |
| `037312282ebc…` | 3 | 0 | 3 | 0.800 | [1] |

### B3 — qid=220 (toxicology)

- Reason: gold_bucket_count_15_gt_wrong_0_but_other_rollout_won
- max_reward=1.0, final_sig=`b01dab9e3058cb19…`, gold_sigs=1

| sig (prefix) | variants | correct | max_bucket | avg_reward | rollouts |
|---|---:|---:|---:|---:|---|
| `b01dab9e3058…` **FINAL** | 71 | 0 | 15 | 0.789 | [1, 3, 5, 6, 7, 8] |
| `b8ea3c76a4a0…` **GOLD** | 49 | 49 | 15 | 0.747 | [1, 2, 3, 4, 5] |

### B5 — qid=27 (california_schools)

- Reason: multiple_gold_signatures_or_v2_split
- max_reward=1.0, final_sig=`9efce191167cfeb5…`, gold_sigs=3

| sig (prefix) | variants | correct | max_bucket | avg_reward | rollouts |
|---|---:|---:|---:|---:|---|
| `93fa7e06c12e…` | 30 | 0 | 15 | 1.000 | [1, 6] |
| `0bcec5eaf4f9…` **GOLD** | 30 | 30 | 15 | 1.000 | [2, 3] |
| `9efce191167c…` **FINAL** | 30 | 0 | 15 | 1.000 | [4, 7] |
| `c961eefe1acc…` **GOLD** | 15 | 15 | 15 | 1.000 | [5] |
| `d4c1f66c140f…` **GOLD** | 15 | 15 | 15 | 1.000 | [8] |

### B6 — qid=136 (financial)

- Reason: residual_selection
- max_reward=1.0, final_sig=`b0529cbd0af60fa8…`, gold_sigs=1

| sig (prefix) | variants | correct | max_bucket | avg_reward | rollouts |
|---|---:|---:|---:|---:|---|
| `dd57b19eb2d1…` **GOLD** | 90 | 90 | 15 | 1.000 | [2, 3, 4, 5, 6, 8] |
| `b3c5b045a729…` | 15 | 0 | 15 | 1.000 | [1] |
| `b0529cbd0af6…` **FINAL** | 15 | 0 | 15 | 1.000 | [7] |


## 3. Fix ROI ranking

| Rank | Bucket | Qids | Complexity | Notes |
|---:|---|---:|---|---|
| 1 | B3 | 12 | Med | column-order rep within bucket |
| 2 | B1 | 10 | Med | reward uses max rollout; elevate rollout with gold selected_sql |
| 3 | B5 | 7 | Med-High | hash v3 merge — only if ≥5 qids |
| 4 | B6 | 6 | — | manual |
| 5 | B2 | 0 | Low | sql_selector tie-break prefers gold cluster on count tie |
| 6 | B4 | 0 | High risk | UCB c affects exploration — may hurt 30q A0 |

## 4. Anti-patterns (do not ship blindly)

- **B4 / UCB**: 30q A0/A3 already tuned with current visit policy; changing `c` can shift search on non-regress qids.
- **B5 / hash v3**: high blast radius on 498q dedup; needs A/B on 30q before scale.
- **B2 tie-break**: safer but only helps tie cases; verify no regression on qids where shorter wrong SQL was intentional tie-break.

> **STOP**: B5 ≥5 — consider hash v3 project.
