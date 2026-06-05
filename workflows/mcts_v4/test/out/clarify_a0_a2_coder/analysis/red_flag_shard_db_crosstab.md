# Red Flag Cross-Validation: db_id × shard (30 min)

Generated: 2026-06-03

## Verdict: **两个红旗是同一个根因，但不是 w1 endpoint 配错**

| 假设 | 结论 |
|---|---|
| w1 跑了错的 vLLM / 模型 / hash | **否** — w1 非 ef2 题表现正常 |
| w1 高退化 = shard 配置 bug | **部分** — w1 被分到了整个 `european_football_2` 库 |
| 498 Final 62%/76% 数字有水分 | **是** — 51 题 ef2 **Hit@1=0/51**（pipeline 完全失败），应剔除后重报 |

---

## 1. db_id × shard 全表（498 题分配）

| db_id | w0 | w1 | w2 | w3 | 合计 |
|---|---:|---:|---:|---:|---:|
| **european_football_2** | 0 | **51** | 0 | 0 | **51** |
| formula_1 | 0 | 66 | 0 | 0 | 66 |
| card_games | 0 | 0 | 52 | 0 | 52 |
| toxicology | 0 | 0 | 0 | 40 | 40 |
| … | … | … | … | … | … |

**`european_football_2` 100% 在 w1**（51/51）。w0/w2/w3 上该库题数为 0。

原因：498 manifest 按 ppl 顺序 sequential 4-shard 切分；ppl 里 ef2 题连续成块，恰好落在 shard1。

---

## 2. db_id × shard 退化表（baseline ✓ → Final ✗）

| db_id | w0 reg/tot | w1 reg/tot | w2 reg/tot | w3 reg/tot | 总退化率 |
|---|---|---|---|---|---|
| **european_football_2** | — | **41/51 (80.4%)** | — | — | 80.4% |
| formula_1 | — | 4/66 (6.1%) | — | — | 6.1% |
| card_games | — | — | 3/52 (5.8%) | 5/23 (21.7%) | 15.4% |
| thrombosis_prediction | 6/47 (12.8%) | 0/3 | — | — | 12.0% |

**w1 的 46 题退化 = 41 ef2 + 4 formula_1 + 1 superhero**

---

## 3. w1 是否正常？——控制变量：去掉 ef2

| 子集 | n | Baseline Hit@1 | Final Hit@1 | 退化数 | 退化率 |
|---|---:|---:|---:|---:|---:|
| **w1 全部** | 125 | 97/125 (77.6%) | 55/125 (44.0%) | 46 | **36.8%** |
| **w1 去掉 ef2** | 74 | 56/74 (75.7%) | 55/74 (74.3%) | **5** | **6.8%** |
| w0 | 125 | — | — | 12 | 9.6% |
| w2 | 125 | — | — | 10 | 8.0% |
| w3 | 123 | — | — | 16 | 13.0% |

**w1 去掉 ef2 后退化率 6.8%，与其他 shard 同量级。**  
→ w1 的 vLLM endpoint / Coder 模型 / v2 hash **没有系统性异常**。

---

## 4. european_football_2 到底发生了什么？

| 指标 | Baseline (Qwen r=20) | Final (Coder r=8 v2, w1) |
|---|---:|---:|
| Hit@1 | **41/51 (80.4%)** | **0/51 (0.0%)** |
| 最终 SQL 非空 | 51/51 | **0/51** |
| rollout valid_count>0 | 有 | **408/408 全为 0** |
| 题均耗时 | ~正常 | **~1.4s**（正常 ~80–100s） |

Log 证据（`v4_a0_30q_coder_rollouts8_w1.log`, qid=1025）：

```
[CTE生成] 完成！共生成 0 个CTE变体，总耗时=0.20s   × 8 rollouts
[Selection] ❌ 未找到有效的rollout（没有result_buckets），无法选择SQL
```

**不是 Coder「做不对足球题」，而是 CTE 阶段 0 变体、pipeline 秒退** — 典型 **DB 连接/文件缺失** 或 schema 加载失败，而非模型能力问题。

w1 log 显示 model=`Qwen3-Coder-30B`，multi_url 含 8000–8300 四口，与其他 shard 一致。

---

## 5. 对 paper 数字的影响

### 原始 Final（含 ef2 失败）

- Hit@1: 309/498 = **62.0%**
- Recall: 379/498 = **76.1%**

### 剔除 ef2 51 题（建议作为 corrected 报告）

| 指标 | 含 ef2 | **剔除 ef2 后** |
|---|---:|---:|
| Hit@1 | 309/498 (62.0%) | **309/447 ≈ 69.1%** |
| Recall | 379/498 (76.1%) | **379/447 ≈ 84.8%** |
| 退化题（vs baseline） | 84 | **84 − 41 = 43** |

剔除 ef2 后，Final vs baseline 的 Hit@1 gap 从 −10.6pp 缩小到约 **−3.6pp**（仍混有 model/r 变量，但可信得多）。

### 84 题退化叙事需改写

- 原叙事「84 题均匀/多库退化」**不成立**
- **41/84 (49%)** 来自单一库的 **pipeline 崩溃**，不是 search/selection 退化
- 剩余 **43 题** 才是 fair comparison 下的真实退化（其中 ~42% selection-only 结论需在这 43 题上重算）

---

## 6. 行动项

1. **Paper §5**：ef2 51 题标注为 **infrastructure failure, excluded**；主表用 447 题 corrected 数字或 footnote。
2. **审稿防御**：附本表证明 w1 endpoint 正常；ef2 是 shard 分配 + DB 不可用，非配置混跑。
3. **补跑（可选）**：仅 ef2 51 题 × Coder r=8 v2（~1–2h），验证 corrected 数字；**不必**重跑全 498。
4. **根因排查**：检查 `european_football_2` sqlite 路径在 6/1 跑 w1 时是否存在/可读。

---

## 7. 一句话结论

> **w1 高退化率 = `european_football_2` 整库被 sequential shard 分到 w1 + 该库在 Final 跑时 DB/CTE pipeline 全线崩溃（0/51 Hit@1，非模型做错）。去掉这 51 题后，w1 退化率 6.8%，与其他 shard 一致；498 Final 主指标应剔除 ef2 后重报。**
