# mcts_v4 中 select_sql（最终选中的 SQL）是怎么选出来的

## 整体流程

1. **每题跑 `rollouts_per_iteration` 条 rollout**（如 20 条），每条 rollout 得到一条路径：  
   Select → Expand → Simulate → 到叶节点后生成多个 SQL 变体 → 执行 → 按结果分桶 → 得到该条 rollout 的 **reward** 和 **selected_sql**。

2. **单条 rollout 内的 selected_sql**（`sql_result_processor.calculate_reward_and_select_sql`）  
   - 对当前路径生成的多个 SQL 执行后，按执行结果分桶（signature）。  
   - reward = 最高一致性（最佳桶的 count / 总变体数）；若最佳桶是「单个 0」结果会惩罚 50%。  
   - 在该条 rollout 内选一条代表 SQL：  
     - 优先在**最佳桶**里，按**列顺序出现次数**选出现最多的列顺序，取对应的一条 SQL；  
     - 否则取最佳 signature 对应的任一条 SQL；  
     - 再否则取第一个执行有效的 SQL。  
   - 这条就是该 rollout 的 `selected_sql`，并写入 `rollout_stats`（含 `result_buckets`、`all_sql_variants` 等）。

3. **整题的最优 SQL（optimal_sql / 你看到的 select_sql）**（`SQLSelector.select_by_highest_reward`，在 `mcts_workflow.solve` 里调用）  
   - **策略名**：选「最高 reward 的 rollout 的 SQL」（max_reward）。  
   - 步骤：  
     1. 过滤出有 `result_buckets` 的 rollout；  
     2. 找到**全局最高 reward**；  
     3. 收集所有达到该最高 reward 的 rollout；  
     4. 对每个这样的 rollout，在其 `result_buckets` 里取 **count 最大的 signature**，再从 `all_sql_variants` 里取该 signature 对应的 SQL，得到多个候选 SQL；  
     5. 若只有一个候选，即为最终 SQL；若有多个候选，**tiebreak**：先比**结果行数少**，再比 **SQL 长度短**，选出一条。  
   - 这条就是写进结果 JSON 顶层的 `sql`（以及 `optimal_sql`），也就是 Hit@1 用来和 gold 比的那条。

## 代码位置

| 步骤 | 文件 | 函数/类 |
|------|------|---------|
| 单条 rollout 内选 SQL | `utils/sql_result_processor.py` | `SQLResultProcessor.calculate_reward_and_select_sql` |
| 整题选最优 SQL | `utils/sql_selector.py` | `SQLSelector.select_by_highest_reward` |
| 调用入口 | `mcts_workflow.py` | `solve()` 中 `optimal_sql = SQLSelector.select_by_highest_reward(rollout_stats_list)` |

## 小结

- **select_sql** = 先按 **reward 最高** 的 rollout(s)，再在这些 rollout 里按 **result_buckets 里 count 最大** 的 signature 取 SQL；若多条 SQL 平票，用**行数少、SQL 短**的 tiebreak。  
- 因此是「自一致性 + 最高 reward + 桶内多数 + tiebreak」，而不是随机或简单取第一条。
