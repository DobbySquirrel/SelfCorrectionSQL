# Selector Replay Results (Step 1)

Generated: 2026-06-03T14:22:02

**Scope**: Phase 1.1 (a0_30 + a3_30) + Phase 1.3 (35 selection-only). **447q deferred.**

## 1. Results matrix (Hit@1 / net vs R0)

### a0_30 (legacy hash)

| Rule | Hit@1 | Δ vs R0 | saved | hurt | net | 30q gate |
|---|---:|---:|---:|---:|---:|---|
| R0_max_reward | 20/30 | +0 | 0 | 0 | +0 | R0 |
| R1_max_cluster_size | 20/30 | +0 | 1 | 1 | +0 | PASS |
| R2_max_cluster_visit | 22/30 | +2 | 2 | 0 | +2 | PASS |
| R3_reward_x_size | 20/30 | +0 | 1 | 1 | +0 | PASS |
| R4_majority_then_reward | 21/30 | +1 | 1 | 0 | +1 | PASS |
| R5_max_cluster_then_visit | 20/30 | +0 | 1 | 1 | +0 | PASS |

### a3_30 (v2 hash)

| Rule | Hit@1 | Δ vs R0 | saved | hurt | net |
|---|---:|---:|---:|---:|---:|
| R0_max_reward | 20/30 | +0 | 0 | 0 | +0 |
| R1_max_cluster_size | 19/30 | -1 | 1 | 2 | -1 |
| R2_max_cluster_visit | 19/30 | -1 | 1 | 2 | -1 |
| R3_reward_x_size | 19/30 | -1 | 1 | 2 | -1 |
| R4_majority_then_reward | 19/30 | -1 | 1 | 2 | -1 |
| R5_max_cluster_then_visit | 19/30 | -1 | 1 | 2 | -1 |

### 35 selection-only (final_498 subset, oracle eval only)

| Rule | Hit@1 | saved vs R0 | hurt vs R0 | net |
|---|---:|---:|---:|---:|
| R0_max_reward | 0/35 | 0 | 0 | +0 |
| R1_max_cluster_size | 18/35 | 18 | 0 | +18 |
| R2_max_cluster_visit | 18/35 | 18 | 0 | +18 |
| R3_reward_x_size | 18/35 | 18 | 0 | +18 |
| R4_majority_then_reward | 18/35 | 18 | 0 | +18 |
| R5_max_cluster_then_visit | 18/35 | 18 | 0 | +18 |

- R0 on 35: **0/35** (ceiling under current rollouts)
- Best net on 35: **R1_max_cluster_size** net **+18**
- O1 est. fixable (B1–B5 union): **29**; gap vs best replay = oracle/water

## 2. Paired diff (vs R0) — a0_30

- **R1_max_cluster_size**: saved=1 ['1524'] | hurt=1 ['1480']
- **R2_max_cluster_visit**: saved=2 ['1498', '1524'] | hurt=0 []
- **R3_reward_x_size**: saved=1 ['1524'] | hurt=1 ['1480']
- **R4_majority_then_reward**: saved=1 ['1524'] | hurt=0 []
- **R5_max_cluster_then_visit**: saved=1 ['1524'] | hurt=1 ['1480']

## 3. 30q health gate (1.1)

- R0 Hit@1 on a0_30: **20/30** (expected ~20/30)
- Eliminated (Hit@1 < R0−1): **none**

## 4. Recommendation

- Candidates passing 30q gate with net≥+1 on a0_30: **['R2_max_cluster_visit', 'R4_majority_then_reward']**
- **447q replay deferred** — review before 1.2.

---

**🛑 STOP (Phase 1.1)**: Review 30q gate before enabling 447q replay.

