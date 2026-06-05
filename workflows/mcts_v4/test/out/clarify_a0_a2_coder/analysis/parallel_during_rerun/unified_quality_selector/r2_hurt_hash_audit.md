# R2 hurt 题 — hash precision vs selector

Generated: 2026-06-03T16:02:04

Scope: R2 hurt n=15 (447 ex-ef2 replay)

## 汇总

| 类别 | n | qids |
|---|---:|---|
| **intra_cluster_v2_collision** (R0/R2 执行 v2 相同, SQL 不同) | 0 | `[]` |
| **selector_cross_cluster** (不同 sig) | 12 | `['12', '117', '215', '273', '424', '728', '868', '897', '1225', '1390', '1464', '1480']` |
| **selector_fixed_tradeoff** (fix_43, 不同 sig) | 3 | `['149', '801', '1257']` |

**Paper 口径（R2, net=+13）**
- 15 hurts → intra-cluster v2 collision **0**, cross-cluster **12**, fixed tradeoff **3**
- 1257/801/897 均为 **cross-cluster**（R0 与 R2 的 v2 不同）

## 明细

| qid | tag | same_v2 | same_sql | fixed | R0 hit | R2 hit |
|---|---|---|---|---|---|---|
| 12 | selector_cross_cluster | False | False | False | True | False |
| 117 | selector_cross_cluster | False | False | False | True | False |
| 149 | selector_fixed_tradeoff | False | False | True | True | False |
| 215 | selector_cross_cluster | False | False | False | True | False |
| 273 | selector_cross_cluster | False | False | False | True | False |
| 424 | selector_cross_cluster | False | False | False | True | False |
| 728 | selector_cross_cluster | False | False | False | True | False |
| 801 | selector_fixed_tradeoff | False | False | True | True | False |
| 868 | selector_cross_cluster | False | False | False | True | False |
| 897 | selector_cross_cluster | False | False | False | True | False |
| 1225 | selector_cross_cluster | False | False | False | True | False |
| 1257 | selector_fixed_tradeoff | False | False | True | True | False |
| 1390 | selector_cross_cluster | False | False | False | True | False |
| 1464 | selector_cross_cluster | False | False | False | True | False |
| 1480 | selector_cross_cluster | False | False | False | True | False |
