# D1.5 — S7 41: R2 top1 cluster 可观测信号（non-oracle）

Generated: 2026-06-04T01:29:46

目的：为 **G1/G2/G3 guard** 提供静态证据，**不用 gold**。

R2 top1 = `total_visit` 最大簇。

- **rollouts@top1**：含该 sig 的 rollout 数（result_buckets）
- **struct_keys**：top1 簇内 SQL 去字面/空白后的结构种类数（G3 代理）

---

## 分组摘要

| 组 | n | top1_visit | visit ratio | rollouts@top1 | struct_keys |
|---|---:|---|---|---|---|
| hurt_1505 | 1 | 5-5 med=5 | 1.2-1.2 | 5-5 | 6-6 |
| saved_r7 (+4) | 4 | 2-10 med=4.0 | 1.0-10.0 | 2-7 | 2-8 |
| recall_no_r7 (263,1486) | 2 | 3-3 med=3.0 | 1.0-1.5 | 3-3 | 3-3 |
| trigger_no_gold (21) | 21 | 3-14 med=5 | 1.0-14.0 | 3-7 | 1-15 |

---

### hurt — 1505（R2✓ R7✗）

| qid | case | top1_visit | top2_visit | ratio | rollouts@top1 | variants | struct_keys | top1_size |
|---|:---|---:|---:|---:|---:|---:|---:|---:|
| 1505 | A | 5 | 4 | 1.2 | 5 | 63 | 6 | 63 |

### saved — R7 救回 4 题

| qid | case | top1_visit | top2_visit | ratio | rollouts@top1 | variants | struct_keys | top1_size |
|---|:---|---:|---:|---:|---:|---:|---:|---:|
| 201 | C | 2 | 2 | 1.0 | 2 | 30 | 2 | 30 |
| 685 | C | 3 | 2 | 1.5 | 3 | 45 | 4 | 45 |
| 1238 | A | 5 | 4 | 1.2 | 4 | 60 | 4 | 60 |
| 1490 | B | 10 | 1 | 10.0 | 7 | 102 | 8 | 102 |

### recall 在池但 R7 未中

| qid | case | top1_visit | top2_visit | ratio | rollouts@top1 | variants | struct_keys | top1_size |
|---|:---|---:|---:|---:|---:|---:|---:|---:|
| 263 | C | 3 | 3 | 1.0 | 3 | 45 | 3 | 45 |
| 1486 | C | 3 | 2 | 1.5 | 3 | 44 | 3 | 44 |

### trigger 无 gold（21，仅摘要）

- visit med 见上表；qids: `25, 37, 48, 50, 145, 169, 412, 533…`

## Guard 可行性（静态）

### G1: top1 `rollouts@top1` ≥ 6 → 保 R2

- 1505: rollouts@top1 = **5**
- saved 4: [2, 3, 4, 7]
- trigger_no_gold: min=3 max=7
- **能否分开 1505 vs saved4（阈值6）**: ❌ 否

### G2: top1/top2 visit ratio > θ → 保 R2

- 1505: ratio = 5/4 = 1.25
- 201: ratio = 1.0 (top2 visit=2)
- 685: ratio = 1.5 (top2 visit=2)
- 1238: ratio = 1.25 (top2 visit=4)
- 1490: ratio = 10.0 (top2 visit=1)
- **1490** ratio≈10 → **G2 会保 R2 且阻止 R7 救回**（你已预警）
- **G2 不适合**作为统一 guard

### G3: top1 `struct_keys` == 1 → 保 R2（簇内 SQL 结构一致）

- 201 (saved_r7): struct_keys=2, variants=30
- 263 (recall_no_r7): struct_keys=3, variants=45
- 685 (saved_r7): struct_keys=4, variants=45
- 1238 (saved_r7): struct_keys=4, variants=60
- 1486 (recall_no_r7): struct_keys=3, variants=44
- 1490 (saved_r7): struct_keys=8, variants=102
- 1505 (hurt_1505): struct_keys=6, variants=63

❌ **G3 不能干净分开** 1505 vs saved4。

## G4 探索（非 oracle，D1.5 后补）

**规则**：若 `top1_struct_keys ≥ 6` **且** `top1_visit/top2_visit < 2` → **保 R2**；否则在触发条件下走裸 R7。

| qid | struct_keys | ratio | G4 分支 |
|-----|------------:|------:|---------|
| 1505 | 6 | 1.25 | **G4 保 R2**（修复 D2a hurt） |
| 201 | 2 | 1.0 | R7 |
| 685 | 4 | 1.5 | R7 |
| 1238 | 4 | 1.25 | R7 |
| 1490 | 8 | **10.0** | R7（ratio≥2 不保护，保留救回） |

**S7 41 上 direct `compare_with_gold`（逐题独立连接，无 cache）**：

| Selector | Hit@1 |
|----------|------:|
| R2 | 1（1505） |
| 裸 R7 | 4（201,685,1238,1490） |
| **G4 + conditional R7** | **5**（上四项 + 1505） |
| hurt vs R2 | **0** |
| saved vs R2 | **+4** |

G4 规则：`top1_struct_keys ≥ 6` 且 `visit_ratio < 2` → 保 R2；否则触发时 R7。  
1505：struct=6, ratio=1.25 → 保护；1490：struct=8 但 ratio=10 → **不保护**，R7 仍救回。

说明：G1/G2/G3 单独不够；**G4 可 non-oracle 落地**。D2b 应 replay **G4+R7**，不是 oracle guard。

⚠️ D2b 禁用 `eval_hit1_sql` 的跨 SQL cache（`norm_sql` 可能误碰撞）。

---

## D1.5 结论 → 是否跑 D2b

1. **G1/G2/G3 单独**：❌ 不能干净分开 1505 vs saved4（G2 还会误杀 1490）。
2. **G4（struct≥6 ∧ ratio<2）**：✅ S7 41 静态可落地；**值得在 498 cache 上 D2b replay G4 版**（非 oracle）。
3. **暂缓** 裸 R7 全 498、MCTS 重跑、75 题 trigger 重叠统计。
