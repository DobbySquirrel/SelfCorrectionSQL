# Stage 2 — Calibrated reward on S7 41q (recall-lost)

Generated: 2026-06-04

JSON: `v4_calib_s7_41_coder_rollouts8.json` (41/41 merged)

Env: `MCTS_USE_SIGNATURE_V2=1`, `MCTS_SELECTOR_STRATEGY=R2`, `MCTS_REWARD_CALIBRATED=1`

Baseline: `v4_final_498q_coder_rollouts8.json` + `recall_map_498_merged.json` — S7 41q 在 baseline 上 **recall=0/41**, **Hit@1 R2 replay=0/41**.

---

## 一句话（与你观察一致）

**最终「纠正」只有 1 题 Hit@1：`1505`**（log 里 `gold_match=True`）。

另有 **7 题** gold SQL **进入** 8-rollout 池（recall↑），但 R2 未把它们选为输出 → Hit@1 仍为 0/41（合并 JSON 里 `optimal_sql` 多为空）。

---

## 主指标：Recall（搜索是否把 gold 搜进池子）

| | Baseline (498) | Calibrated (重跑 41q) | Δ |
|---|---:|---:|---|
| Recall | **0/41** | **7/41** | **+7** |

**Recovered qids**（baseline recall false → calib 任一路径对）:

| qid | Cluster case |
|-----|----------------|
| 201 | C |
| 263 | C |
| 685 | C |
| 1238 | A |
| 1486 | C |
| 1490 | B |
| 1505 | A |

Case 分布：**A=2, B=1, C=4**（偏 **C** 多 cluster，与「压 fake reward=1.0」假设一致）。

**Recall hurt**: 0

---

## 次指标：Hit@1（R2 最终选择）

| | Count |
|---|---:|
| `stats.gold_match` | **1/41** (`1505`) |
| 合并 JSON **`sql`** 字段重评 | **1/41**（与上一致） |

**更正（C 排查）**：JSON 落盘用 **`sql`**，不是 `optimal_sql`；41/41 均有 `sql`。此前读 `optimal_sql` 为空属**字段名误读**，非 merge bug。详见 `calib_s7_41_post_cb.md`。

---

## 与 Stage 1 的交叉

- **1505**：30q Stage 1 **hurt** vs a3 R2；S7 重跑 **recall 救回 + 唯一 Hit@1** → calibration 在「搜索层」和「选择层」效应可分离。
- **1486**：30q **saved**；S7 **recall 救回**，Hit@1 仍未中。

---

## Decision gate

| 条件 | 结果 |
|------|------|
| Recall 救回 ≥ 10 | ❌ **7** |
| Recall 救回 5–9 | ✅ **7** → **REVIEW**（不调参先定论） |
| Recall 救回 0 | ❌ |
| Hit@1 大幅提升 | ❌ 仅 1/41 |

**Verdict: ⚠️ MARGINAL — calibration 在 S7 上验证了「搜索/recall +7」，未验证「selection 闭环」；不建议自动 Stage 3 全 498。**

可选下一步（需你批）：

1. **修 `optimal_sql` 落盘** + 对 7 recall 题做 selector replay（R2 是否能把 7 里更多推成 Hit@1）
2. **调 penalty**（0.10/0.20）再跑 S7 41q A/B（仅当要坚持 reward 线）
3. **Paper**：S7 上 reward calibration 改善 exploration/recall，Hit@1 仍受 R2 visit 与 model ceiling 限制

🛑 **停步 — 等 review 后再定 Stage 3 / 498。**
