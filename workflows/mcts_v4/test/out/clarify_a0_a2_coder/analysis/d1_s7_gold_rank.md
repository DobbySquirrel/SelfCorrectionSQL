# D1 — S7 六题 gold cluster 在 R2 排序中的名次

Generated: 2026-06-04T01:21:39

数据：`v4_calib_s7_41_coder_rollouts8.json`（calibrated 重跑，不重跑 MCTS）

题集：recall✓ Hit@1✗ 的 6 题（不含已中的 1505）

R2 排序键：`total_visit`（与 `pick_r2` 一致）。Gold cluster = 池内至少一条 SQL 与 gold 执行一致所属 signature。

## qid=201 (case C, high_reward_rollouts=7)

- **Gold rank (best)**: **2** / 4 clusters (all gold ranks: [2, 4])
- **Bucket**: `rank2_R7_candidate`
- R2 pick hit gold: False | R7 pick hit gold: True
- R7 trigger (≥6 high-reward & ≥2 clusters): True

| R2 rank | sig | visit | size | max_r | gold? |
|---:|---|---:|---:|---:|:---:|
| 1 | `a271bf0020a2…` | 2 | 30 | 1.000 |  |
| 2 | `857b830c58bf…` | 2 | 30 | 1.000 | ✓ |
| 3 | `15b629d772be…` | 2 | 30 | 1.000 |  |
| 4 | `a142d1f50024…` | 1 | 15 | 1.000 | ✓ |

## qid=263 (case C, high_reward_rollouts=8)

- **Gold rank (best)**: **4** / 4 clusters
- **Bucket**: `rank3_5_need_new_signal`
- R2 pick hit gold: False | R7 pick hit gold: False
- R7 trigger (≥6 high-reward & ≥2 clusters): True

| R2 rank | sig | visit | size | max_r | gold? |
|---:|---|---:|---:|---:|:---:|
| 1 | `5acf2c7d607a…` | 3 | 45 | 1.000 |  |
| 2 | `519e3af65f24…` | 3 | 30 | 1.000 |  |
| 3 | `ecbc03d4e80f…` | 3 | 30 | 1.000 |  |
| 4 | `dc5e07579ad7…` | 1 | 15 | 1.000 | ✓ |

## qid=685 (case C, high_reward_rollouts=8)

- **Gold rank (best)**: **2** / 5 clusters (all gold ranks: [2, 3, 4])
- **Bucket**: `rank2_R7_candidate`
- R2 pick hit gold: False | R7 pick hit gold: True
- R7 trigger (≥6 high-reward & ≥2 clusters): True

| R2 rank | sig | visit | size | max_r | gold? |
|---:|---|---:|---:|---:|:---:|
| 1 | `9869a1e7ebbc…` | 3 | 45 | 1.000 |  |
| 2 | `37f6b23fca4c…` | 2 | 30 | 1.000 | ✓ |
| 3 | `50fa54f4a8cc…` | 1 | 15 | 1.000 | ✓ |
| 4 | `77e0b7e1a569…` | 1 | 15 | 1.000 | ✓ |
| 5 | `44a78859e863…` | 1 | 15 | 1.000 |  |

## qid=1238 (case A, high_reward_rollouts=8)

- **Gold rank (best)**: **2** / 2 clusters
- **Bucket**: `rank2_R7_candidate`
- R2 pick hit gold: False | R7 pick hit gold: True
- R7 trigger (≥6 high-reward & ≥2 clusters): True

| R2 rank | sig | visit | size | max_r | gold? |
|---:|---|---:|---:|---:|:---:|
| 1 | `d3f13d852212…` | 5 | 60 | 1.000 |  |
| 2 | `a126a45a738b…` | 4 | 60 | 1.000 | ✓ |

## qid=1486 (case C, high_reward_rollouts=7)

- **Gold rank (best)**: **3** / 5 clusters
- **Bucket**: `rank3_5_need_new_signal`
- R2 pick hit gold: False | R7 pick hit gold: False
- R7 trigger (≥6 high-reward & ≥2 clusters): True

| R2 rank | sig | visit | size | max_r | gold? |
|---:|---|---:|---:|---:|:---:|
| 1 | `f1794ed39075…` | 3 | 44 | 1.000 |  |
| 2 | `eab1c107c943…` | 2 | 30 | 1.000 |  |
| 3 | `e67a1a9e223e…` | 2 | 16 | 1.000 | ✓ |
| 4 | `80f2c2ccae4e…` | 1 | 15 | 1.000 |  |
| 5 | `771cc96cd8a2…` | 1 | 15 | 1.000 |  |

## qid=1490 (case B, high_reward_rollouts=7)

- **Gold rank (best)**: **2** / 2 clusters
- **Bucket**: `rank2_R7_candidate`
- R2 pick hit gold: False | R7 pick hit gold: True
- R7 trigger (≥6 high-reward & ≥2 clusters): True

| R2 rank | sig | visit | size | max_r | gold? |
|---:|---|---:|---:|---:|:---:|
| 1 | `d9d44abf50ae…` | 10 | 102 | 1.000 |  |
| 2 | `b638f1930b4c…` | 1 | 15 | 1.000 | ✓ |

## 汇总

| Gold rank bucket | n | qids |
|---|---:|---|
| `rank2_R7_candidate` | 4 | 201, 685, 1238, 1490 |
| `rank3_5_need_new_signal` | 2 | 263, 1486 |

- **rank 2（R7 候选）**: 4/6
- **rank 3–5**: 2/6
- **tail / 无 sig**: 0/6

- **R7 replay 在 6 题上 Hit@1**: 4/6 → ['201', '685', '1238', '1490']

## D2 建议（基于上表）

- **rank 2 居多** → 值得在「S7-detector + 池内有 gold」子集上试 **conditional R7**（勿全 498）。
- 注：静态 R7 replay 已命中 ['201', '685', '1238', '1490']，与全 498 上 R7 -19 不矛盾（子集有效）。

不重跑 498；不扫 reward penalty。