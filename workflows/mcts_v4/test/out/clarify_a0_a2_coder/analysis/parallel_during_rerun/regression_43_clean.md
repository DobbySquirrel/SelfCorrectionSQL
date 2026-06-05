# Regression 43 Clean Analysis (P1)

Generated: 2026-06-03T13:56:13

Definition: baseline Hit@1 ✓ → Final Hit@1 ✗, **excluding ef2 51 qids** (infra silent failure).

Compare: replaces `regression_84_breakdown.md` for paper §5 / §6.3.

## 1. Net Hit@1 change (ef2 excluded from regression count)

| Metric | Count |
|---|---:|
| Baseline→Final regressions (raw 84) | 84 |
| ef2 infra regressions (subset of 84) | 41 |
| **True regressions (43 target)** | **43** |
| Fixed (baseline ✗ → Final ✓, non-ef2) | 31 |
| Fixed (all, incl. ef2 overlap) | 31 |
| **Net Hit@1 (paper, excl. ef2 regressions)** | **+31 − 43 = -12** |
| Net Hit@1 (raw 84 count) | +31 − 84 = -53 |

## 2. Four-dimension distribution (43 true regressions)

### A — db_id

| db_id | regressions | total q | regress rate | share of 43 |
|---|---:|---:|---:|---:|
| card_games | 8 | 52 | 15.4% | 18.6% |
| toxicology | 7 | 40 | 17.5% | 16.3% |
| thrombosis_prediction | 6 | 50 | 12.0% | 14.0% |
| superhero | 5 | 52 | 9.6% | 11.6% |
| debit_card_specializing | 4 | 30 | 13.3% | 9.3% |
| formula_1 | 4 | 66 | 6.1% | 9.3% |
| codebase_community | 3 | 49 | 6.1% | 7.0% |
| student_club | 2 | 48 | 4.2% | 4.7% |
| california_schools | 2 | 30 | 6.7% | 4.7% |
| financial | 2 | 30 | 6.7% | 4.7% |

**vs 84-q breakdown**: top db was `european_football_2` at 41/84 (48.8% of regressions). After ef2 removal: **41** ef2 regressions stripped; max single-db share now **18.6%** (card_games, 8 q).

> Concentration >50% on one DB: **NO ✓**

### B — Gold SQL complexity

| bucket | count (43) | % | count in 84 (ref) |
|---|---:|---:|---:|
| simple | 26 | 60.5% | 60 |
| medium | 14 | 32.6% | 18 |
| complex | 3 | 7.0% | 6 |

- **Simple regressions**: 26 (60.5% of 43) vs 60 in 84 (71.4%)

### C — Recall state (Final on 43 regressions)

| Category | Count | % of 43 |
|---|---:|---:|
| Recall lost (search failure) | 8 | 18.6% |
| Recall retained (selection-only) | 35 | 81.4% |

**Paper §1 quantitative claim**: **35** questions still have a correct SQL path in Final but wrong Hit@1 — clarification/selection target.

vs 84-q recall split: 49 search / 35 selection (58.3% / 41.7%).

### D — Shard (498 4-shard, post-ef2)

| shard | regress (84 era) | shard n | rate (84) | regress (clean) | non-ef2 n | rate (clean) |
|---|---:|---:|---:|---:|---:|---:|
| w0 | 12 | 125 | 9.6% | 12 | 125 | 9.6%
| w1 | 46 | 125 | 36.8% | 5 | 74 | 6.8%
| w2 | 10 | 125 | 8.0% | 10 | 125 | 8.0%
| w3 | 16 | 123 | 13.0% | 16 | 123 | 13.0%

- Mean clean regress rate: **9.3%** (43 spread over ~447 non-ef2 q ≈ 9.6% global)
- w1 clean rate: **6.8%** (84-era w1 was 36.8% with ef2)

> w1 rate normalized after ef2 removal — **endpoint OK**.

## 3. Five hard questions

| qid | in 43 regress? | db_id | Final Hit@1 | Final recall | max clusters |
|-----|:--------------:|-------|:-----------:|:------------:|-------------:|
| 1472 | — | debit_card_specializing | False | True | 1 |
| 1482 | — | debit_card_specializing | True | True | 2 |
| 1498 | ✓ | debit_card_specializing | False | False | 1 |
| 1529 | — | debit_card_specializing | False | False | 1 |
| 1531 | — | debit_card_specializing | False | False | 1 |

**1529**: recall=✗, Hit@1=✗ → **SEARCH FAILURE**

> **🛑 STOP**: 1529 is not selection-only — §1 motivating example must change.
## 4. Diff vs `regression_84_breakdown.md`

- Regression count: 84 → **43** (−41 ef2 infra, all in `european_football_2`).
- ef2 share of raw regressions: 41/84 = 48.8%.
- db concentration: european_football_2 41/84 (48.8%) → top `card_games` 8/43 (18.6%).
- Recall split: 49/35 (58/42%) → **8/35** (19/81%) — selection-only **+0** questions.
- Simple complexity: 60/84 → 26/43 (60% of clean regress).
- w1 shard rate: 36.8% (46/125) → **6.8%** clean (ef2 was w1-heavy).

## 5. Paper §5 / §6.3 narrative draft (~180 字)

剔除 european_football_2 的 41 题基础设施退化后，真实 Hit@1 退化为 43 题（净变化 +31−43=-12）。退化不再集中于单一 mega-schema 库，而呈多库长尾；其中 35 题（81%）Final 仍保留正确执行路径但选错答案，是 clarification 的主要量化靶点。简单题仍占退化主体（26 题），说明不少失败来自选择而非搜索深度不足。分片层面 w1 退化率在去掉 ef2 后回落至 6.8%，支持「w1 异常由 ef2 context 触发」而非端口故障。

---

P1 complete. Recommend review before P2 cost profile.

