# S7 Cluster Distribution Audit (41 q)

Generated: 2026-06-03T22:10:49

Population: recall-lost 75 中 **primary=S7**（8 rollout 中 ≥6 个 reward≥0.99 且 recall=False）。

## 1. 情况 A/B/C 归类（按 rollout 终态 result cluster）

| 情况 | 定义 | n | % |
|---|---|---:|---:|
| **A** | 8 rollout **同一** result cluster（终态签名相同） | 9 | 22.0% |
| **A′** | A 且 ≥6 rollout reward≥0.99（S7 规则核心） | 9 | 22.0% |
| **B** | 2–3 个错 cluster（终态） | 21 | 51.2% |
| **C** | ≥4 个终态 cluster（搜索分散但都错） | 11 | 26.8% |

## 2. Depth-1 CTE 多样性（与 R1 S2 规则同源）

| depth-1 distinct clusters ≤2 | 5/41 (12.2%) |
| depth-1 distinct clusters =1 | 2/41 |

解读：depth-1 同质化 ≠ 终态同一 cluster；候选 4 应对的是 **终态 trapped in one wrong cluster**（A/A′）。

## 3. 情况 D（recall-lost 池内）

本 41 题 **oracle recall=False** → 任一路径 `selected_sql` 均未 gold match。
**不存在**「有正确 cluster 仅 visit 低被 R2 忽略」——那是 selection-only（已在 recall✓ 池）。

## 4. 补充：SQL 文本同质性

| 指标 | n/41 |
|---|---:|
| 8 条 rollout `selected_sql` 完全相同 | **2** |
| 8 条 dominant `result_buckets` 签名相同 | **9** |
| ≥6 条 reward≥0.99（S7 定义） | **41** |

**结论**：S7 的主信号是 **「高 consistency reward + 全错」**，不是「8 rollout 挤在一个错 cluster」。与「局部单峰」叙事只有 **~9 题** 完全吻合。

## 5. 候选 4 对症度

- **A/A′（9 题）**：force rerun + 高温最对症。
- **B（21 题，51%）**：2–3 个错 cluster；rerun 可能进第 4 个签名，仍值得试但假设要写成「高 reward 错方向」而非「单 cluster 陷阱」。
- **C（11 题）**：已 ≥4 cluster，搜索并不窄；rerun **边际偏低**（除非专门拉高 temperature 造新 cluster）。
- **触发器** `all_rollouts_in_one_cluster && size≥6` 只覆盖 **~22%** S7；若上候选 4，建议改为 **`high_reward_rollouts≥6 && recall=False`**（覆盖 41 题）或 **union_buckets≤2**（约 9+12=21？需再滤）。

## 6. 样例（A′ 单 cluster + 高 reward）

- q212 (toxicology): rollout_clusters=1, d1=3, high_r=8/8
- q383 (card_games): rollout_clusters=1, d1=2, high_r=8/8
- q671 (codebase_community): rollout_clusters=1, d1=1, high_r=8/8
- q794 (superhero): rollout_clusters=1, d1=7, high_r=8/8
- q1238 (thrombosis_prediction): rollout_clusters=1, d1=4, high_r=8/8
- q1256 (thrombosis_prediction): rollout_clusters=1, d1=5, high_r=6/8
- q1302 (thrombosis_prediction): rollout_clusters=1, d1=3, high_r=8/8
- q1389 (student_club): rollout_clusters=1, d1=5, high_r=8/8

## 7. 全量 qid × cluster 数

| qid | db | rollout_clusters | union_buckets | d1 | high_r | case |
|---:|---|---:|---:|---:|---:|---|
| 25 | california_schools | 4 | 4 | 15 | 8 | C |
| 37 | california_schools | 2 | 2 | 4 | 8 | B |
| 48 | california_schools | 2 | 2 | 4 | 8 | B |
| 50 | california_schools | 7 | 7 | 11 | 8 | C |
| 145 | financial | 3 | 3 | 4 | 8 | B |
| 169 | financial | 2 | 1 | 6 | 7 | B |
| 201 | toxicology | 5 | 5 | 8 | 8 | C |
| 212 | toxicology | 1 | 1 | 3 | 8 | A |
| 263 | toxicology | 4 | 4 | 8 | 8 | C |
| 383 | card_games | 1 | 1 | 2 | 8 | A |
| 412 | card_games | 4 | 3 | 10 | 6 | C |
| 533 | codebase_community | 2 | 2 | 4 | 8 | B |
| 557 | codebase_community | 2 | 2 | 2 | 8 | B |
| 640 | codebase_community | 3 | 4 | 8 | 7 | B |
| 671 | codebase_community | 1 | 1 | 1 | 8 | A |
| 685 | codebase_community | 4 | 4 | 9 | 7 | C |
| 772 | superhero | 3 | 3 | 10 | 6 | B |
| 794 | superhero | 1 | 1 | 7 | 8 | A |
| 894 | formula_1 | 6 | 6 | 5 | 8 | C |
| 901 | formula_1 | 3 | 3 | 7 | 8 | B |
| 904 | formula_1 | 3 | 4 | 10 | 6 | B |
| 948 | formula_1 | 2 | 2 | 9 | 8 | B |
| 1002 | formula_1 | 3 | 2 | 7 | 7 | B |
| 1037 | european_football_2 | 2 | 3 | 4 | 7 | B |
| 1080 | european_football_2 | 2 | 2 | 7 | 7 | B |
| 1136 | european_football_2 | 2 | 3 | 6 | 6 | B |
| 1169 | thrombosis_prediction | 2 | 2 | 6 | 8 | B |
| 1238 | thrombosis_prediction | 1 | 1 | 4 | 8 | A |
| 1243 | thrombosis_prediction | 2 | 2 | 15 | 8 | B |
| 1252 | thrombosis_prediction | 2 | 2 | 6 | 8 | B |
| 1256 | thrombosis_prediction | 1 | 2 | 5 | 6 | A |
| 1302 | thrombosis_prediction | 1 | 1 | 3 | 8 | A |
| 1357 | student_club | 5 | 6 | 6 | 7 | C |
| 1359 | student_club | 3 | 4 | 9 | 6 | B |
| 1389 | student_club | 1 | 1 | 5 | 8 | A |
| 1486 | debit_card_specializing | 7 | 7 | 12 | 8 | C |
| 1490 | debit_card_specializing | 2 | 3 | 2 | 6 | B |
| 1498 | debit_card_specializing | 2 | 2 | 5 | 8 | B |
| 1505 | debit_card_specializing | 1 | 1 | 1 | 8 | A |
| 1529 | debit_card_specializing | 5 | 5 | 9 | 8 | C |
| 1531 | debit_card_specializing | 4 | 4 | 7 | 8 | C |
