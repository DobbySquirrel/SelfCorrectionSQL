# Alpha-SQL Setup Alignment (R2)

Generated: 2026-06-03

Sources: `Alpha-SQL-2.2.4/config/arcwise_config.yaml`, `alphasql/algorithm/mcts/reward.py` (read-only).

## 1. 配置对照表

| 维度 | Alpha-SQL (arcwise) | 当前 v4 + R2 (Coder r=8) | 差异 |
|---|---|---|---|
| Rollout 步数 | `max_rollout_steps=24` | `rollouts_per_iteration=8` | 语义不同：24 步 MCTS 模拟 vs 8 条并行轨迹 |
| 树深度 | `max_depth=16` | `max_depth=8` | 我们 CTE 深度上限更低 |
| 探索常数 | `exploration_constant=1.414` | workflow UCB / visit | 同族，实现不同 |
| 每步 LLM | `n=3`, `temperature=0.8` | CTE 并行 `parallel_workers=5` | 我们单次 expansion 更重 |
| Reward | `MajorityVoteRewardModel` → `consistency_score` | `result_buckets` 一致性 reward | 同为 **DB execution consistency**，非 LLM self-eval |
| 调 DB | **是** | **是** | 都不是纯 LLM reward |
| 最终选择 | MCTS END + selection runner | `SQLSelector` **R2** max cluster visit | 我们显式 cluster-visit patch |
| Signature | majority / 结果分组 | **v2 hash** (`MCTS_USE_SIGNATURE_V2=1`) | final 498 已开 v2 |

## 2. Reward 函数（1 段）

`MajorityVoteRewardModel` 对 END 节点返回 **`end_node.consistency_score`**：子 SQL 执行结果与当前 END 结果的多数一致性。注释路径亦曾用 **frozenset(执行结果) 多数票**。与 v4 的 bucket consistency **同族**，不是 step-level LLM 打分。

## 3. 1.5 min vs 10 min 归因（粗分解）

| 因素 | 贡献方向 |
|---|---|
| r=24 vs r=8 | ~3× 计数，**不足以解释 6–7×** wall |
| 更深 action 链（schema/keyword/SQL gen/revision） | Alpha 单题步数多 |
| 我们 CTE + complete SQL 变体 + 大 DDL prompt | 单次 expansion 更重 |
| vLLM **无 prefix cache**（见 R3） | CTE 重复 DDL ~10–25% |
| 每线程新建 OpenAI client（见 `perf_baseline.md`） | 5–15% |

## 4. 可借鉴 idea（动不变量?）

| Idea | 动 H1? | 动 30q? | 老师? |
|---|---|---|---|
| consistency 与 selector 对齐加强 | 否 | 否 | 否 |
| max_rollout_steps / depth ↑ | **是** | 是 | 可选 |
| schema/keyword 预处理 action | 部分 | 否 | prompt |
| n=3 每步采样 → K↑ | **是** | 是 | 否 |
| prefix cache + shared HTTP client | **否** | 否 | 否 |
