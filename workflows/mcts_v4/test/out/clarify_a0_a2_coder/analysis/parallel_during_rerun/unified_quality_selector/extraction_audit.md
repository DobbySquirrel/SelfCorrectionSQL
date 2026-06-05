# Extraction / Rollout Quality Audit (Phase 2)

Generated: 2026-06-03T14:43:18

> `empty_rollout` = valid_count==0 ∧ cte_path==[] ∧ result_buckets=={}
> （覆盖 F2 提取失败、context 秒退、搜索无产物；与 T2 silent exception 同形）

## 1. Per-dataset rates

| dataset | n_q | n_rollout | empty_rollout % | all-8-empty q % | anomaly (vc>0,no cte) |
|---|---:|---:|---:|---:|---:|
| final_498 | 498 | 3984 | 10.62% | 10.24% | 0 |
| ef2_rerun | 21 | 168 | 0.60% | 0.00% | 0 |
| a0_30 | 30 | 240 | 0.00% | 0.00% | 0 |
| a3_30 | 30 | 240 | 0.00% | 0.00% | 0 |
| a0_30_r20 | 30 | 600 | 0.00% | 0.00% | 0 |
| baseline_498 | 498 | 9960 | 1.30% | 0.20% | 0 |

## 2. 35 selection-only × empty rollout

| Metric | Value |
|---|---|
| selection-only n | 35 |
| ≥1 empty rollout | 0 (0.0%) |
| R2 saved 18 polluted | 0/18 (0.0%) |
| Adjusted upper (R2 saved, 0 empty rollout) | **18** |

## 3. Decision gateway

**Verdict: PASS** — 进 Phase 3 跑 447

| Pollution band | Action |
|---|---|
| 0–10% | Phase 3 447 replay OK |
| 10–30% | 用 adjusted 净值 |
| >30% | 延期 selector，先修 extraction |

---

**🛑 STOP after Phase 2 — await review before Phase 3.**

