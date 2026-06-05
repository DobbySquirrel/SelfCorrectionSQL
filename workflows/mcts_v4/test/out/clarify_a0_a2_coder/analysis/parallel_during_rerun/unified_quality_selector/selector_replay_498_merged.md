# Selector Replay — 498 merged (Phase 3-bis)

Generated: 2026-06-03T16:38:10

**Data**: `v4_final_498q` with **51 ef2 qids** replaced by `v4_ef2_51_rerun_coder_rollouts8.json`.
**Selector**: `SQLSelector.select()` (same code path as `mcts_workflow`; set `MCTS_SELECTOR_STRATEGY=R2` in production).

## 1. Main table (R0 vs R2 focus)

| Rule | Hit@1 | Recall | Saved | Hurt | Net | Δ Hit@1 vs R0 |
|---|---:|---:|---:|---:|---:|---:|
| R0_max_reward | 350/498 (70.3%) | 423/498 (84.9%) | 0 | 0 | +0 | +0 |
| R1_max_cluster_size | 364/498 (73.1%) | 423/498 (84.9%) | 28 | 14 | +14 | +14 |
| R2_max_cluster_visit | 364/498 (73.1%) | 423/498 (84.9%) | 30 | 16 | +14 | +14 |
| R3_reward_x_size | 364/498 (73.1%) | 423/498 (84.9%) | 28 | 14 | +14 | +14 |
| R4_majority_then_reward | 364/498 (73.1%) | 423/498 (84.9%) | 28 | 14 | +14 | +14 |
| R5_max_cluster_then_visit | 364/498 (73.1%) | 423/498 (84.9%) | 28 | 14 | +14 | +14 |

- **Stored** `stats.gold_match` on merged JSON: **350/498** (70.3%)
- R0 replay: **350/498** | R2 replay: **364/498** | R2 net vs R0: **+14**
- Winner (net tie-break): **R2_max_cluster_visit**

## 2. ef2 51 subset (rerun rollout data)

| Metric | old final (51) | merged stored | R0 replay | R2 replay |
|---|---:|---:|---:|---:|
| Hit@1 | 0 | 41 | 41 | 42 |
| R2 saved vs R0 on ef2 | — | — | — | 2 |
| R2 hurt vs R0 on ef2 | — | — | — | 1 |

## 3. R2 paired diff (498)

- **saved** (30): `['28', '118', '125', '136', '186', '230', '232', '268', '397', '414', '416', '462', '528', '694', '766', '773', '792', '884', '962', '972', '1001', '1139', '1144', '1153', '1208', '1235', '1241', '1506', '1524', '1525']`
- **hurt** (16): `['12', '117', '149', '215', '273', '424', '728', '801', '868', '897', '1115', '1225', '1257', '1390', '1464', '1480']`

## 4. Compare to 447 ex-ef2 (Phase 3)

| Pool | R0 Hit@1 | R2 Hit@1 | R2 net |
|---|---:|---:|---:|
| 447 (old final, ex-ef2) | 309/447 | 322/447 | +13 |
| **498 merged** | 350/498 | 364/498 | +14 |

> 447 数字来自 `selector_replay_447_cache.json`（ef2 未替换）。498 合并后 R0/R2 会随 ef2 修复上移。

