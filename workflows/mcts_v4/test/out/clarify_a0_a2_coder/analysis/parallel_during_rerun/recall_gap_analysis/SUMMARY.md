# Recall Gap Analysis — SUMMARY

Generated: 2026-06-03

输出目录：`recall_gap_analysis/`（R1 screen 会话名 `recall_r1_taxonomy`，日志 `r1_taxonomy.log`）

---

## 1. R1 主桶分布（75 recall-lost）

```
S7 ████████████████████████████████████████████ 41 (54.7%)
S4 ██████████████████                           18 (24.0%)
S5 █████████                                     9 (12.0%)
S6 ████                                          4 ( 5.3%)
S0 ██                                            2 ( 2.7%)
S3 █                                             1 ( 1.3%)
S1 S2                                           0 ( 0.0%)
```

- **423/498 recall (84.9%)** → **75 lost**（与 `recall_map_498_merged.json` 一致）
- **S0=2**（<15）→ 分桶规则可接受，残差 `346, 407`
- **S5+S7=50 (≥30)** → 🛑 **Stop：reward redesign 应上升为老师层讨论主路径**

---

## 2. R2 Alpha-SQL 关键差异（3 行）

1. Reward 同为 **DB execution + consistency**，不是纯 LLM self-eval。  
2. 主要结构差：**r=24 / depth=16** vs 我们 **r=8 / depth=8**；时间差还含 **大 DDL prompt + 无 prefix cache**。  
3. 可不动不变量先做：**prefix cache + HTTP client 复用**；动 H1 才考虑 r/depth/K。

---

## 3. R3 perf 摸底结论

- Coder vLLM **未开** `--enable-prefix-caching` / `--enable-chunked-prefill`。  
- 建议 diff 已写在 `vllm_perf_baseline.md`；**本次不重启**，等无 MCTS 重跑窗口再验 `cached_tokens>0`。

---

## 4. 综合决策表

| 修复方向 | R1 救题估算 | 动不变量? | 老师? | ROI |
|---|---|---|---|---|
| Reward / cluster 对齐（S7+S5） | 高（50/75 命中规则） | **是** | **是** | 高，但需设计评审 |
| DDL trim / schema（S4） | 中（~18 题标签） | maybe | 否 | 中高，偏 infra |
| prefix cache + client 复用 | 0 Hit@1，降 wall | **否** | 否 | 高（纯 perf） |
| max_depth / K↑（S1/S2=0） | 低（本池未命中） | 是 | 可选 | 低优先级 |
| Model ceiling（S6） | ~4 题 | no | paper | 写 limitation |
| R2 selector 再调 | 已 +14 Hit@1 | 否 | 否 | 边际 |
| **R6a / R7 replay** | R6a -3 / R7 -19 vs R2 | 否 | 否 | **R7 否决** |

---

## 4b. R6a / R7 selector replay（498 + 30q + S7）

| Rule | Hit@1 (498) | Net vs R2 | 结论 |
|---|---:|---:|---|
| R2 | 364 | 0 | baseline |
| R6a | 361 | **-3** | S7 上等同 R2（high_r→R2），无救 |
| R7 | 345 | **-19** | 🛑 太激进；a0_30 仅 17/30 |

**决策**：**不落地 R6a/R7**；selection 对 S7 饱和 → 走路径 **C（reward）** 或 S7 子集 **候选 4（extra rollout）**。

---

## 5. 推荐路径

**A — 守不变量（推荐先做）**  
S4 DDL/prompt 瘦身 + **启用 prefix cache**（重启窗口另议）+ shared HTTP client。不动 reward / r / hash。

**B — 半放开（老师拍板）**  
A + r↑ 或 K↑；本 75 题分桶 **S1/S2=0**，对 recall-lost 池预期收益有限。

**C — 重设计（R1 Stop 触发）**  
**S7(41)+S5(9)=50** → reward 与 cluster 一致性、高 reward 错结果（S7）为主战场；需老师评审后再动 H1/30q。

---

## 6. 不做的事

- ❌ 改 `sql_selector.py` / 主 workflow（本轮分析已结束）  
- ❌ 重启 vLLM（除非你确认窗口）  
- ❌ 重跑 MCTS / 30q controlled exp  
- ❌ 凭猜测填 Alpha-SQL 数字（仅 repo/paper 有据部分）

---

## 7. 文件清单

| 文件 | 说明 |
|---|---|
| `recall_lost_75_taxonomy.md` / `.json` | R1 分桶 |
| `recall_map_498_merged.json` | 498 oracle recall 缓存 |
| `alpha_sql_setup.md` | R2 |
| `vllm_perf_baseline.md` | R3 |
| `run_r1_in_screen.sh` | 可复现 screen 启动 |
| `r1_taxonomy.log` | R1 日志 |
| `selector_r67_498_merged.md` | R6a/R7 498 replay |
| `selector_r67_s7_breakdown.md` | S7 41 题子集 |
| `selector_r67_30q_sanity.md` | 30q gate |

**状态：R1–R3 + R6a/R7 replay 已交付；selector 层勿再加规则，优先 reward / infra。**
