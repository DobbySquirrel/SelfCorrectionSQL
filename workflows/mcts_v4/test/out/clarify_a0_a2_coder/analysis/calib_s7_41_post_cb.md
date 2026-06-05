# Stage 2 跟进 — C（落盘）+ B（R2 replay）

Generated: 2026-06-04

## C. `optimal_sql` 落盘排查 — 非 merge bug

| 检查项 | 结果 |
|--------|------|
| 合并 JSON 41/41 均有 **`sql`** 字段（非空） | ✅ |
| 字段名 | 管道写入 **`sql`**（`test_mcts.py` L119/193），**无** `optimal_sql` 键 |
| 此前报告读 `optimal_sql` | ❌ 误读 → 显示「全空」 |
| `1505` | `sql` 238 字符，`stats.gold_match=True` |
| `stats.gold_match` vs `sql` 重评 | **0 mismatch** |

**结论 C：Hit@1 = 1/41 为真**（仅 1505），不是落盘压低。无需重跑 MCTS。

建议在下游分析统一：`predicted = rec.get("sql") or rec.get("optimal_sql")`。

---

## B. 7 题 recall 池 × R2 replay

Baseline 498：S7 41q recall **0/41**。Calibrated 重跑：recall **7/41**。

| qid | case | gold 在池 | pipeline `sql` Hit@1 | R2 replay Hit@1 |
|-----|------|-----------|----------------------|-----------------|
| 201 | C | ✅ | ❌ | ❌ |
| 263 | C | ✅ | ❌ | ❌ |
| 685 | C | ✅ | ❌ | ❌ |
| 1238 | A | ✅ | ❌ | ❌ |
| 1486 | C | ✅ | ❌ | ❌ |
| 1490 | B | ✅ | ❌ | ❌ |
| 1505 | A | ✅ | ✅ | ✅ |

- **R2 replay 全集**：**1/41**（仅 1505），与 pipeline 一致。
- **6/7 recall 救回题**：search 已把 gold 放进 `all_sql_variants`，**R2 visit 未选出** → 瓶颈在 **selector**，不是「池里没有」。

---

## 修正后的 Stage 2 总表

| 指标 | 值 |
|------|-----|
| Recall Δ | 0 → **7/41** |
| Hit@1（`sql` / R2 replay） | **1/41** |
| Search-only 收益 | **+6** 题（recall✓, Hit@1✗） |

## Gateway（不变）

| 条件 | 结果 |
|------|------|
| Recall ≥ 10 | ❌ (7) |
| 5–9 | ✅ REVIEW |
| Hit@1 因落盘修复而跃升 | ❌ C 已否定 |

**不建议 Stage 3 全 498。** 若继续投入：优先 **selector 层**（R2′ / 在 recall✓ 子集上试 R6a 等），而非再扫 reward penalty。
