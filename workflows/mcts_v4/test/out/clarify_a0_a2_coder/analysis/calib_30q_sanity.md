# Stage 1 — Calibrated reward 30q sanity

Generated: 2026-06-04 (post-merge eval, **gate corrected**)

Calib JSON: `workflows/mcts_v4/test/out/clarify_a0_a2_coder/v4_calib_30q_coder_rollouts8.json`

Env: `MCTS_USE_SIGNATURE_V2=1`, `MCTS_SELECTOR_STRATEGY=R2`, `MCTS_REWARD_CALIBRATED=1`

## 比较口径（重要）

| Baseline | Hash | 是否与 calibrated 同 config |
|---|---|---|
| a0 R2 = 22/30 | **legacy** | ❌ 跨 hash，**不作门禁** |
| **a3 R2 = 19/30** | **v2** | ✅ **唯一有效对照** |

Calibrated 使用 v2 hash，**只与 a3 R2 比**。此前 “vs a0 -1” 为跨 hash 伪信号（与早期 a3 vs a0 noise 同类）。

## 同 config 结果（vs a3 R2）

| 指标 | a3 R2 | Calibrated | Δ |
|---|---:|---:|---:|
| Hit@1 | 19/30 | **21/30** | **+2** |
| Recall | 26/30 | 26/30 | +0（30q recall 已 saturate ~86.7%） |
| Saved | — | 1486, 1525, 1533 | |
| Hurt | — | **1506**（仅 1 题） | |
| Net | — | **+2** | |

参考（非门禁）：a0 R2 22/30 legacy；a0 R0 20/30。

**Calibration 已生效**：multi-bucket rollout 出现非 legacy reward（0.51、0.68 等）。

## Decision gate（修正后）

| 条件（v2 / vs a3） | 结果 |
|---|---|
| calibrated Hit@1 ≥ a3 R2 (19) | ✅ **21/30** |
| 退化 ≥ 2 题 | ✅ 仅 1 hurt (1506) |
| Recall 在 30q 上提升 | N/A（ceiling 26/30） |

**Verdict: ✅ Stage 1 PASS → 批准 Stage 2（S7 41 题 recall-lost）**

Spike：`analysis/calib_1506_dump.md`（1506 hurt 机制，不阻塞 Stage 2）。

🛑 Stage 3（全 498）仍待 Stage 2 结果 + user review。
