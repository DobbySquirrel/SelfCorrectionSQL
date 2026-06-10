# Diverse mode C — 30q pipeline sanity

Generated: 2026-06-08T15:07:33

Diverse JSON: `workflows/mcts_v4/test/out/cte_diverse/v4_diverse_c_30q_coder_rollouts8.json`
Baseline: `workflows/mcts_v4/test/out/clarify_a0_a2_coder/v4_calib_30q_coder_rollouts8.json`

## Gate metrics

| metric | calib 30q | diverse-C 30q |
|---|---:|---:|
| recall (exec-equiv) | 26/30 | **28/30** |
| Hit@1 (R2, exec-equiv) | 21/30 | **21/30** |
| mean CTE candidates / decompose node | ~5 | **10.42** |
| fallback rate (diverse→temp) | — | **0.0%** (0/368) |

- recall >= baseline: ✓
- Hit@1 >= baseline - 2: ✓ (need >= 19)
- fallback <= 20%: ✓

**Overall: PASS**

## 时间统计 (JSON stats.timing, 每题 solve)

| 指标 | diverse-C mean | diverse-C sum | calib mean | Δ mean |
|---|---:|---:|---:|---:|
| total_s | 310.4 | 9313 | 111.2 | +199.2 |
| rollout_s | 310.4 | 9312 | 111.2 | — |
| sql_gen_s | 33.0 | — | 12.9 | — |
| db_exec_s | 10.18 | — | 4.65 | — |

### 分片 wall clock (log 起止)

- w0: **69.5 min** (4169s)
- w1: **52.2 min** (3134s)
- w2: **33.8 min** (2025s)
- w3: **18.8 min** (1129s)
- 并行总耗时（最慢片）≈ **69.5 min**

## Cluster 边际递减 — diverse 三次 LLM call（结构签名）

每 decompose expand 事件内，按 temp 调用顺序 (0.3 → 0.6 → 0.9) 统计：

| call | temp | mean raw CTE | mean **新增** struct | mean 累计 unique struct | 边际效率 (new/5) |
|---:|---:|---:|---:|---:|---:|
| 1 | 0.3 | 4.99 | **4.99** | 4.58 | 99.7% |
| 2 | 0.6 | 5.00 | **3.17** | 7.62 | 63.3% |
| 3 | 0.9 | 4.97 | **2.88** | 10.42 | 57.6% |

- struct dedupe 后进执行池 mean: **10.42** CTE/expand

**解读**：若边际递减成立，call 3 的「新增 struct」应明显低于 call 1。

## Cluster 边际递减 — 执行结果分桶（log `[去重统计]`）

- expand 事件数（log 桶行）: **368**
- 每桶 mean / median: **7.54** / 8.0
- 前1/3 expand mean 桶数: **8.35**
- 中1/3 expand mean 桶数: **7.49**
- 后1/3 expand mean 桶数: **6.78**

（执行分桶 = 真正进 MCTS 树的 cluster 数；随搜索深入，候选间结果重复率通常上升。）

**结构边际比** call3/call1 新增 struct = **0.58×**
