# MCTS v4 方法逻辑整理

## 一、整体框架（对齐 CET-SQL Algorithm 2 + Alpha-SQL MCTS）

- **问题拆分**：先把原始问题 Q 分解成子问题列表 SQ = [q1, q2, ..., qn]，深度 = 子问题个数。
- **每个节点**：对应一个子问题；节点内对当前子问题「生成 CTE → 执行 → M_verify」直到 valid 或最多 3 次迭代，再按执行结果分桶建子节点。
- **Select / Expand / Simulate / Backprop**：与 Alpha-SQL 一致——从根 UCB 选到叶；在叶上扩展；Simulate 时**随机**选子节点直到终态；整条路径回传同一个 reward。

## 二、入口与初始化

1. **入口**：`MCTSWorkflow.solve(question, schema_info, ...)`，当 `use_decompose_flow=True` 时走 v4。
2. **初始化（仅 v4）**：
   - 调用 `QuestionDecomposer.decompose(question, schema_info, additional_context)` 得到 `sub_questions`。
   - 挂到根节点：`root_node.sub_questions = sub_questions`，`root_node.sub_question_index = -1`。
   - `max_depth = min(len(sub_questions), 原 max_depth)`。

## 三、单次 Rollout（_execute_mcts_rollout_v4）

| 步骤 | 方法 | 含义 |
|------|------|------|
| 1. **Select** | `_select_to_leaf_v4()` | 从根出发，未访问子节点优先，否则 UCB1 选子，直到当前树的**叶节点**（无子或 terminal）。得到 path。 |
| 2. **终态判断** | `_is_terminal_v4(path)` | 路径已覆盖所有子问题（每个子问题都有 valid 节点）或当前叶已是 terminal → 可生成完整 SQL。 |
| 3a. **终态** | — | 直接 `_reward_from_path_v4(path)` 得 reward 和 selected_sql，再 `_mcts_backpropagation(path, reward)`，本 rollout 结束。 |
| 3b. **非终态** | `_expand_leaf_v4(leaf)` | 在叶上扩展：对当前子问题生成 CTE → 执行 → **M_verify**（CTESufficientChecker.verify）→ 通过则按执行结果分桶建子节点；不通过则节点内迭代（最多 3 次）。 |
| 4. **Simulate** | `_simulate_v4(child)` | 从扩展出的子中 **random 选一个** child，再从该 child 起反复「未扩展则 _expand_leaf_v4，再 random 选子」，直到终态，得到 sim_path。 |
| 5. **Reward** | `_reward_from_path_v4(sim_path)` | 用路径上 CTE 链，CompleteSQLGenerator 生成多条完整 SQL → 执行 → 按结果分桶，reward = 最大桶计数/变体数。 |
| 6. **Backprop** | `_mcts_backpropagation(sim_path, reward)` | 沿 sim_path 从叶回溯到根，每个节点 N+=1、Q+=reward。 |

## 四、节点内扩展（_expand_leaf_v4）

- 取当前子问题：`sub_questions[leaf.sub_question_index]`，以及从根到 leaf 的**前缀执行历史** `h_prefix`（已解决子问题的 q、cte、result_summary）。
- **最多 3 轮**迭代：
  1. `_generate_cte_variants(leaf, failed_attempts_v4=...)` 生成当前子问题的 CTE 变体（首轮为空，之后传入 M_verify 不通过时的 cte+reason）。
  2. 执行、去重后，对每个 CTE 调用 `CTESufficientChecker.verify(原始问题, 当前子问题, h_prefix, 当前 CTE, 执行结果)`。
  3. 若有 **valid=True**：按执行结果签名分桶，为每个桶建一个子节点（子节点带下一子问题下标、cte、execution_results），打乱子节点顺序，标记 `leaf.is_expanded = True`，结束扩展。
  4. 若 **valid=False**：把 (cte, reason, result_summary, execution_preview) 加入 `failed_attempts_for_gen`，下一轮再生成。
- 若 3 轮后仍无 valid 但有执行成功的 CTE，则**兜底**接受一个桶，避免无子节点。

## 五、多 Rollout 与最终输出

- `solve()` 内按 `rollouts_per_iteration` 执行多次 `_execute_mcts_rollout_v4()`，得到多条 rollout 的 reward、selected_sql、all_sql_variants。
- **最优 SQL**：`SQLSelector.select_by_highest_reward(rollout_stats_list)`（reward 最高的一条的 selected_sql）。
- 返回：`optimal_sql`、`rollout_stats`、`all_sqls_with_attributes`、`sub_questions`（v4 问题拆分结果）。

## 六、与 v1 的差异（简要）

| 项目 | mcts_v1 | mcts_v4 |
|------|--------|--------|
| 深度 | 固定 max_depth | 由子问题个数决定 |
| 节点语义 | 每层 = 一步 CTE | 每节点 = 一个子问题 |
| 节点内 | 生成一批 CTE → 执行分桶 → 选一个扩展 | 生成 CTE → **M_verify** → 通过则分桶建子，不通过则迭代（最多 3 次） |
| Simulate | 按桶权重随机选 | 从扩展出的子中 **random 选**（与 Alpha-SQL 一致） |
