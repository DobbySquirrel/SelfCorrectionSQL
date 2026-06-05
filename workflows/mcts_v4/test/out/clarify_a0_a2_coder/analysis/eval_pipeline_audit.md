# Eval Pipeline Audit — Hit@1 口径对齐

Generated: 2026-06-04

## Ground truth（锁定）

**Hit@1 = R2 replay**：`pick_r2(build_clusters(rollout_stats))` + `compare_with_gold`（D2b / `selector_replay_498_merged.md` 同口径）。

`stats.gold_match` 是 MCTS **落盘时**写入，仅当 `stored sql == pick_r2` 时方可等同 R2 Hit@1。

---

## 1. 三口径对照

| Dataset | A: gold_match | stored sql == R2? | C: R2 Hit@1 | 来源 |
|---|---:|---|---:|---|
| **final_raw** | 309/498 | **217/498** 不一致 | （需 replay） | raw `v4_final_498q` |
| **merged_ef2** | 350/498 | **241/498** 不一致 | **364/498** | D2b ✅ |
| **calib_498** | 370/498 | **0/498** 全一致 | **370/498** | sql≡R2 → gm 可代 |

### 364 vs 309 根因（红旗 1 关闭）

| 数字 | 实际含义 |
|---|---|
| **364** | `load_merged_498()`（final + ef2 51 题 overlay）上的 **R2 replay** |
| **309** | raw final JSON 上的 **`stats.gold_match`**，且 217 题 stored sql ≠ R2 pick |

**结论：364 不是虚高，309 不是 R2 baseline。** 差因 = 数据集（ef2 overlay）+ 指标（落盘 gm vs selector replay）+ stored/R2 不一致。

---

## 2. calib Hit@1 vs merged baseline

| | R2 Hit@1 |
|---|---:|
| merged_ef2 (baseline) | **364/498** (73.1%) |
| calib_498 | **370/498** (74.3%) |
| **Δ** | **+6** |

Calib 跑时 `MCTS_SELECTOR_STRATEGY=R2`，498/498 题 `stored sql == pick_r2`，故 `gold_match=370` 即为 R2 Hit@1。

⚠️ 此前用 raw final `gold_match=309` 当 baseline 得出「+61」是**口径错误**；正确 baseline 是 merged R2 **364**，净 **+6**。

---

## 3. 冻结规则

1. Baseline Hit@1 = **merged_ef2 R2 replay = 364**
2. Calib Hit@1 = **370**（R2 跑，sql≡pick_r2）
3. 禁止用 raw final `gold_match` 与 calib 比 Hit@1
4. Recall 单独报（single-run vs single-run）

---

## 4. Audit 脚本注记

`eval_pipeline_audit.py` 全量 1180 `(qid,sql)` eval 在 ~95% 处因慢查询卡住；phase 1 `gold_match` 与 D2b R2=364 已足够关闭红旗 1。全量三口径 DB replay 可后续补跑。
