# Quality Summary (Task 3)

| Config | n | r | Hash | Hit@1 | Recall | gap (pp) |
|---|---:|---:|---|---|---:|---:|---:|
| 30q A0 r=8 legacy | 30 | 8 | legacy | 20/30 (66.7%) | 25/30 (83.3%) | 16.7 |
| 30q A3 r=8 v2 | 30 | 8 | v2 | 20/30 (66.7%) | 25/30 (83.3%) | 16.7 |
| 30q A0 r=20 legacy | 30 | 20 | legacy | 21/30 (70.0%) | 25/30 (83.3%) | 13.3 |
| 498q Final r=8 v2 | 498 | 8 | v2 | 309/498 (62.0%) | 379/498 (76.1%) | 14.1 |
| 498q Baseline r=20 legacy | 498 | 20 | legacy | 362/498 (72.7%) | 431/498 (86.5%) | 13.9 |

## Key observations

### Recall − Hit@1 gap

- **30q A0 r=8 legacy**: gap = **16.7pp**
- **30q A3 r=8 v2**: gap = **16.7pp**
- **30q A0 r=20 legacy**: gap = **13.3pp**
- **498q Final r=8 v2**: gap = **14.1pp**
- **498q Baseline r=20 legacy**: gap = **13.9pp**

### r=8 → r=20 (30q Coder)

- Hit@1: 20/30 → 21/30 (**+1**)
- Recall: 25/30 → 25/30 (**+0**)
- Extra rollouts improved final selection on 1 question only; exploration ceiling unchanged on 30q.

### 498q Final vs historical baseline (⚠ mixed: Coder r=8 v2 vs Qwen r=20 legacy)

- Hit@1: 62.0% vs 72.7% (**-10.6pp**)
- Recall: 76.1% vs 86.5% (**-10.4pp**)
- Gap stable: 14.1pp vs 13.9pp

## Paper narrative (§5 draft)

> Across rollout budgets r in {8, 20} on our 30-question sanity set and at 498-question scale, the Recall - Hit@1 gap remains approximately **14 percentage points** on full dev (498q) and **~17pp** on the 30q Coder subset. Increasing r from 8 to 20 yields modest Hit@1 gains on 30q (+1 question) without expanding recall, indicating that **rollout budget alone cannot close the selection gap** — motivating clarification as an orthogonal mechanism.

## Data gaps

- **r=2**: not available — cannot plot three-point rollout curve with r=2.
- **300q**: not available (per user).

