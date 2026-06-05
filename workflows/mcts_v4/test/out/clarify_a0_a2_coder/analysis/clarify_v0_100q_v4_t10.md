# AutoClarify v0 — T10 Lift gold-in-pool (triggered 61)

- Generated: 2026-06-04T16:24:00.674581+00:00
- Triggered qids: v4 trace (`clarify_v0_log_only_100q_v4.trace.jsonl`), **N=61**
- Gold match: `compare_with_gold` via P0 cache (`p0_union_recall.json`)
- Pools: calib=`v4_calib_498q_coder_rollouts8.json`, final=`v4_final_498q_coder_rollouts8.json`, ef2=`v4_ef2_51_rerun` (51 qids)

## T10.1 Lift table

| pool | gold_in_pool / 61 |
|---|---:|
| calib only | 21/61 (34.4%) |
| final only | 23/61 (37.7%) |
| ef2 only (51-q subset) | 1/61 (1.6%) |
| calib ∪ final | 30/61 (49.2%) |
| **calib ∪ final ∪ ef2** | **30/61 (49.2%)** |

Note: T9 used `normalize_sql` on calib `rollout_stats` only → **0/61**. T10 uses execution-equivalence (`compare_with_gold`) on full pool index (same as P0).

**Critical cross-check (rollout_stats pool only, same pool v0 enforcer uses):**

| match method | gold_in_pool / 61 |
|---|---:|
| `normalize_sql` on rollout variants (T9) | 0/61 |
| `compare_with_gold` on rollout variants | **20/61** |
| `compare_with_gold` on full calib record | 21/61 |

→ T9 saved=0 narrative holds for **string-normalize Hit@1**, but **execution-equivalent gold exists in 20/61 rollout pools**. R1 / oracle re-analysis should use `compare_with_gold`.

### Lift beyond calib (triggered subset)

- final adds over calib: **9** qids — `31, 72, 186, 234, 530, 788, 915, 1166, 1254`
- ef2-only adds (not in calib/final): **0** — ``
- any union lift over calib-only: **9** qids

## T10.2 Per-bucket lift (union calib∪final∪ef2)

Buckets = s8_100q stratified sample, intersected with triggered 61.

| bucket | n (triggered∩bucket) | gold_in_union | rate |
|---|---:|---:|---|
| calib_only | 6 | 6 | 6/6 (100.0%) |
| final_only | 9 | 9 | 9/9 (100.0%) |
| missed_by_all | 19 | 0 | 0/19 (0.0%) |
| S7_subset | 12 | 0 | 0/12 (0.0%) |
| R2_hit_random | 15 | 15 | 15/15 (100.0%) |

## R1 go/no-go gate

| T10.1 union gold_in_pool | **30/61** |
| Verdict | ≥20 → **R1 / 多 seed 可行**，派 R1b 设计单 |

### Read

- 不同 run/seed 已在 triggered 子集上采到 gold → **R1（cluster ban / 多 seed regenerate）有数据基础**。
- calib single-run 仅 21/61，union 提升到 30/61 → 多 run 有 **+9** 题可及 gold（若能把那些 run 的 SQL 并进 pool）。
