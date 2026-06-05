# P0 Selector Replay Audit

Generated: 2026-06-03T14:37:18

## 1. Is this pure replay? (no MCTS re-run)

| Check | Result |
|---|---|
| Reads existing JSON only | **YES** — `load_json` → `rollout_stats` |
| Re-runs CTE/SQL generation | **NO** — no `MCTSWorkflow`, no LLM calls in replay |
| Re-runs DB for selection | **NO** — `compare_with_gold` only in `eval_hit1_sql` after pick |
| Changes stored rollout_stats | **NO** — read-only |

**Conclusion**: Replay = re-apply selection rules on frozen rollouts. **Minutes, not hours.**

## 2. Is R2 oracle-free?

R2 definition in code:
```python
best_sig = max(clusters, key=lambda s: clusters[s].total_visit)
return _tiebreak_pick(clusters[best_sig].variants)  # min rows, min len
```

### Static scan (pick_* functions)

| function | oracle-free? |
|---|:---:|
| `pick_r0` | ✓ |
| `pick_r1` | ✓ |
| `pick_r2` | ✓ |
| `pick_r3` | ✓ |
| `pick_r4` | ✓ |
| `pick_r5` | ✓ |
| `build_clusters` | ✓ |
| `_tiebreak_pick` | ✓ |

**Gold is used only in `eval_hit1_sql` after SQL is chosen** — not in any `pick_*`.

### ⚠️ Methodology caveat: `total_visit` aggregation

In `build_clusters`, each rollout adds `leaf_visit_count` to **every** signature
in that rollout's `result_buckets`. If one rollout has multiple sig keys,
the same visit is credited to each — **visit is not per-cluster in the tree sense**.
R2 is still oracle-free, but the feature definition may differ from MCTS UCB visits.

## 3. 35 selection-only: R2 saved 18 qids

Count: **18** (from replay cache `sel35.R2_max_cluster_visit.saved_qids`)

This is **not** '51% of all BIRD' — it is **18/35** on the regression subset where
baseline Hit@1✓→Final Hit@1✗ and recall✓. **Independent from a3_30** (different qids).

## 4. Exemplar cluster tables (3 qids)

### qid=136 (financial)

- Rollouts: 8
- **R2 picks sig** `dd57b19eb2d184b7…` with **total_visit=13**
- Runner-up visits: [('b3c5b045a72991db', 1), ('b0529cbd0af60fa8', 1)]
- R2 pick = argmax(total_visit)? **True**
- Post-hoc eval: R0 hit=False, R2 hit=True

| sig | count | **visit** | max_r | variants | gold_variants (post-hoc) | R2? | rep_hit |
|-----|------:|--------:|------:|-------:|-------------------------:|:---:|:-------:|
| `dd57b19eb2d184b7…` | 90 | **13** | 1.0 | 90 | 90 | **Y** | True |
| `b3c5b045a72991db…` | 15 | **1** | 1.0 | 15 | 0 |  | False |
| `b0529cbd0af60fa8…` | 15 | **1** | 1.0 | 15 | 0 |  | False |

### qid=186 (financial)

- Rollouts: 8
- **R2 picks sig** `5420c8037875b7a0…` with **total_visit=5**
- Runner-up visits: [('0b7fdd7763bff8e7', 1), ('0cebc35c31d8463a', 1), ('aea2dbfefe7d8428', 1)]
- R2 pick = argmax(total_visit)? **True**
- Post-hoc eval: R0 hit=False, R2 hit=True

| sig | count | **visit** | max_r | variants | gold_variants (post-hoc) | R2? | rep_hit |
|-----|------:|--------:|------:|-------:|-------------------------:|:---:|:-------:|
| `5420c8037875b7a0…` | 61 | **5** | 1.0 | 61 | 16 | **Y** | True |
| `0b7fdd7763bff8e7…` | 15 | **1** | 1.0 | 15 | 0 |  | False |
| `0cebc35c31d8463a…` | 14 | **1** | 0.9333 | 14 | 0 |  | False |
| `aea2dbfefe7d8428…` | 1 | **1** | 0.9333 | 1 | 0 |  | False |
| `52344221f48cfd6c…` | 14 | **1** | 0.9333 | 14 | 14 |  | True |

### qid=230 (toxicology)

- Rollouts: 8
- **R2 picks sig** `dfb1f11ddccaae61…` with **total_visit=7**
- Runner-up visits: [('35ea0f951dd13c61', 3), ('5168eaf0ca3ac07b', 1)]
- R2 pick = argmax(total_visit)? **True**
- Post-hoc eval: R0 hit=False, R2 hit=True

| sig | count | **visit** | max_r | variants | gold_variants (post-hoc) | R2? | rep_hit |
|-----|------:|--------:|------:|-------:|-------------------------:|:---:|:-------:|
| `dfb1f11ddccaae61…` | 75 | **7** | 1.0 | 75 | 75 | **Y** | True |
| `35ea0f951dd13c61…` | 30 | **3** | 1.0 | 30 | 0 |  | False |
| `5168eaf0ca3ac07b…` | 15 | **1** | 1.0 | 15 | 0 |  | False |

## 5. P0 verdict

| Question | Verdict |
|---|---|
| Pure replay? | **PASS** |
| R2 uses gold in selection? | **PASS** (gold only in eval) |
| 18/35 is oracle ceiling? | **NOT pure ceiling** — R2 does not read gold; 18 is replay upper bound under **this visit definition** |
| a3_30 -1 vs 35 +18 contradiction? | **RESOLVED** — different qid sets; do not cross-compare |
| Ready for 447 replay? | **YES** (minutes) if you approve P1 |

---

**🛑 P0 complete — await approval before 447 full replay.**

