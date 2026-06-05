# T2 Findings: european_football_2 单题 Failure 模式

Generated: 2026-06-03  
**🛑 Hard checkpoint — 请 review 后再决定是否继续 T3/T4/T5/T6**

---

## 1. 抽样题（5 题，前/中/后）

| qid | total_s | cte_gen_s | sql_gen_s | db_exec_s | rollout_count | sql 空? |
|-----|--------:|----------:|----------:|----------:|--------------:|:------:|
| 1025 | 1.43 | 1.43 | 0.00 | 0.00 | 8 | ✓ |
| 1028 | 1.49 | 1.49 | 0.00 | 0.00 | 8 | ✓ |
| 1094 | 1.34 | 1.34 | 0.00 | 0.00 | 8 | ✓ |
| 1147 | 1.47 | 1.47 | 0.00 | 0.00 | 8 | ✓ |
| 1148 | 1.46 | 1.46 | 0.00 | 0.00 | 8 | ✓ |

**51/51 ef2 题模式一致**（非抽样偶然）。

---

## 2. Rollout 状态（5 题共同模式）

| 字段 | 值 |
|------|-----|
| `rollout_stats` 长度 | 8 |
| 每 rollout `valid_count` | **0** |
| 每 rollout `result_buckets` | **{}** (len=0) |
| 每 rollout `error_reason` | **None** |
| 每 rollout `cte_path` | **[]** |
| 每 rollout `leaf_depth` | **0** |
| 每 rollout `selected_sql` | **""** |
| 最终 `sql` | **""** |

→ 8 个 rollout 均在 **depth=0 第一次 expansion** 即终止，从未进入 DB 执行或 complete SQL 阶段。

---

## 3. 1.4s 时间分配重建

```
total_s ≈ 1.43s
├── rollout_s ≈ 1.43s  (100%)
│   └── cte_gen_s ≈ 1.43s  (100%)
│       └── 8 rollouts × ~0.18s/rollout
│           └── 每 rollout 1 次 expansion
│               └── 4 个 temperature 组并行 LLM 调用
│                   └── 每组 ~0.15–0.20s → **0 个 CTE 变体**
├── sql_gen_s = 0
└── db_exec_s = 0
```

| 阶段 | 耗时 | 结论 |
|------|------|------|
| schema load / tree init | ≪0.1s（无单独字段） | 正常进入 CTE 阶段 |
| 第一次 LLM call | ~0.15–0.20s × 8 | **瞬时失败**，无 `temperature=… 耗时=…` 成功日志 |
| CTE → DB execute | 0 | 无变体可执行 |
| complete SQL | 0 | 未到达 simulation |
| selection | ≪0.01s | `未找到有效的rollout` |

**关键对比（同 shard w1，前一题 qid=1302 thrombosis）**：

```
[CTE生成] temperature=0.9, group_size=1, 耗时=2.95s   ← 正常 LLM
[CTE提取] 找到CTE名称: cte1
...
total_s ≈ 80–100s
```

ef2 题 **从未出现** `temperature=` 行 → LLM 调用在 `client.chat.completions.create()` 内 **异常退出**。

---

## 4. Log 证据（qid=1025 / 1094 / 1147）

**最后一行「成功操作」**（ef2 每题相同）：

```
>>> 样本#128 qid=1025 DB=european_football_2
[CTE生成] 开始并行生成 5 个CTE变体（4个temperature组）...
[CTE生成] 完成！共生成 0 个CTE变体，总耗时=0.20s    ← ×8 rollouts
[Selection] ❌ 未找到有效的rollout（没有result_buckets），无法选择SQL
```

**未发现**：

- ❌ Traceback / Exception 打印（被代码吞掉）
- ❌ `sqlite3.OperationalError` / `FileNotFoundError` / `Connection refused`
- ❌ 任何 CTE 提取 / DB 执行统计行

**w1 配置（log 头部，与其他 shard 一致）**：

- model: `Qwen3-Coder-30B`
- vllm: `8000/8100/8200/8300` 四口
- rollouts=8, seed=20240601

---

## 5. 根因定位（T2 定性）

### 5.1 失败步骤

**CTE 生成阶段 LLM 调用全部 silent fail** → 0 variants → MCTS 8 rollout 空转 → 空 SQL。

代码位置（框架缺陷）：

```python
# workflows/mcts_v4/agents/cte_generator.py ~L1428
            except Exception:
                return []   # ← 异常被吞，无 log，无写入 error_reason
```

### 5.2 最可能触发原因：**超大 schema → LLM context 超限**

| | european_football_2 | thrombosis_prediction (正常) |
|---|---:|---:|
| `ddl_data` 长度 | **~102 KB** | ~7.5 KB |
| Final 题均耗时 | ~1.4s | ~80–100s |
| CTE 变体 | 0 | 5+ |

ef2 的 DDL 含大量 column value statistics，是 dev 集中最大的 schema 之一。  
~102KB 文本 + system prompt + FORCE_S2 strategy injection → 极易超过 vLLM `max_model_len`（尤其 Coder-30B 部署若为 8k/16k）。

**支持证据**：

- 失败 **确定性**（51/51，每次 ~0.15s 秒退）→ 典型 API reject，非随机 LLM 质量
- **同 shard** 其他库正常 → 非 w1 endpoint 配错
- **当前 host** 上 `build_db_connector('european_football_2')` 可正常查表 → DB 文件 **现在** 可达（T3 预检）

**反证 / 待 T3 确认**：

- 若 6/1 跑时 DB 短暂不可用，应看到 CTE execute 失败而非 LLM 0.15s 秒退；当前证据更指向 **LLM 层**

### 5.3 Baseline 对照（T4 预读，供 review 参考）

同 51 题 ef2，**Qwen3-32B r=20 baseline**（Mar 2026）：

| 指标 | 值 |
|------|-----|
| Hit@1 | 41/51 |
| sql 为空 | **0/51** |
| wall time median | **169.8s** |
| P95 / max | 337s / 507s |

→ **同一库、同一 ~102KB schema，baseline 能跑通**；Final 失败是 **Coder 部署 / context 配置** 问题，不是「足球库本质上不可做」。

---

## 6. 框架改进清单（供 review 后实施）

| # | 问题 | 建议 |
|---|------|------|
| F1 | `except Exception: return []` 吞掉所有 LLM 错误 | 至少 `logger.warning(exc)` + 写入 `rollout_stats.error_reason` |
| F2 | `error_reason=None` + 空 SQL 仍写入「完成」结果 | 增加 `failure_mode: llm_error \| db_error \| selection_empty` |
| F3 | 无 preflight prompt 长度检查 | 发送前估算 token；超限则 truncate schema 或 abort with explicit flag |
| F4 | 无 silent failure 告警 | `total_s < 5 && sql==""` → 标记 `infra_failure=True`，acc 脚本自动 exclude |
| F5 | acc 汇总不区分 0/51 崩溃 vs 真错 | paper 表需 footnote + 自动 exclude list |

---

## 7. T2 结论（一句话）

> **ef2 51 题在 CTE 第一次 LLM 调用处被 silent exception 清空（0 变体 / 1.4s / 无 DB），根因高度疑似 ~102KB schema 触发 Qwen3-Coder-30B vLLM context 上限；非 w1 shard 配错，而是框架不记录 LLM 失败导致整库静默归零。**

---

## 8. Review 后路径建议

| T2 结论 | 下一步 |
|---------|--------|
| 认可 LLM context 根因 | 跳 T5 silent scan + T6 报告；可选补跑 ef2（调 max_model_len 或 truncate） |
| 需确认 DB | 做 T3 文件可达性 |
| 需量化 baseline 差异 | 做 T4（已有预读数据） |

**请确认是否继续 T3–T6。**
