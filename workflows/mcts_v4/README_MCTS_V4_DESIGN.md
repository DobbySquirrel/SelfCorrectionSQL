# MCTS v4 设计说明：问题拆分 + 节点内迭代

本目录由 `mcts_v1` 复制而来，用于实现「先问题拆分、按子问题深度扩展、节点内 CTE 迭代至满意」的新流程。保留 v1 不动便于对比效果。

**对齐论文**：设计对齐 CET-SQL 的 Self-Feedback Generation Module 中的 **Subquery Iteration Generation（Algorithm 2）**：先分解子问题 SQ，对每个子问题「生成 → 执行 → 验证(valid) → 不通过则迭代」，用执行历史 H 组合最终 SQL。

## 扩展 / 选择 / 模拟：类 Alpha-SQL 逻辑

mcts_v4 的 MCTS 部分按 **Alpha-SQL** 的方式即可（参见 `Alpha-SQL-2.2.4/alphasql/algorithm/mcts/mcts.py`）：

- **Select**：从根出发，在已建好的树上用 UCB（或未访问子节点优先）选到当前树的**叶**（尚无子节点的节点或已 terminal）。
- **Expand**：只在该叶节点上扩展一层（生成该节点的所有合法 children，如按执行结果分桶得到的子节点）。
- **Simulate**：从扩展出的 children 里**随机选一个**，再沿该子节点反复「expand → 随机选一个 child」直到终态（END / 所有子问题解决）。
- **Backpropagate**：从终节点算 reward，沿路径回溯到根，对路径上每个节点做 N+=1、Q+=reward。

逻辑上的「层」可对应子问题序号（第 i 层 = 子问题 i），树仍是在叶节点处、沿路径生长的。

**下一轮选什么？** 每一轮都**从根重新 Select**。例如第一轮路径是 root → node1 → node2 → node3 → 终态 → 回传；扩展 node1 时若按分桶建了多个子节点（node2、node1.1、node1.2…），则**没被选中的 node1.1、node1.2 等仍在树上**，只是还没有被扩展（没有自己的 children），所以是当前树的**叶**。下一轮：从根出发用 UCB 选子节点，可能再次选到 node1，再在 node1 的子节点里用 UCB 选——若选到 **node1.1**（未访问或 UCB 高），则 node1.1 就是本轮的叶，在 **node1.1 上扩展**，再 simulate、回传。因此「下一轮从 node1.1 开始扩展」是对的：先选到 node1.1（从根一路 UCB 下来），再在 node1.1 上 expand。**Alpha-SQL 就是这套逻辑**：每轮 `select(root_node)` 从根选到叶，未访问子节点优先（`child.N == 0` 则直接返回），未选中的兄弟留在树上，下一轮可能被选到并扩展（见 `mcts.py` 的 `solve` 与 `select`）。

## CET-SQL 与 mcts_v4 的对应关系

### Algorithm 2: Subquery Iteration Generation → mcts_v4 主流程

| CET-SQL 步骤 | mcts_v4 对应 |
|-------------|--------------|
| Decompose: SQ = {q1,...,qn} ← M(Q, S) | **问题拆分**：`sub_questions` ← LLM(question, schema)，深度 = len(SQ) |
| H ← ∅（执行历史） | 路径上的节点序列 = 执行历史 H：每节点存 (q_i, cte_i, r_i, valid) |
| for each q_i: repeat 生成→执行→验证 until valid | **每个节点**：对当前子问题 q_i 生成 CTE → 执行 → **M_verify**（当前 CTE 能否解决 q_i）→ valid 则节点完成，否则迭代（最多 3 次） |
| sql_final ← M(Q, H) | **最终路径**上根据整题 + 路径上所有 (q, cte, r) 用 CompleteSQLGenerator 生成完整 SQL |

- **M_verify(Q, q_i, H_prefix, sql_i, r_i)**：即节点内的 **Check**，输入原始问题 Q、当前子问题 q_i、前缀执行历史 H_prefix、当前 CTE/SQL、执行结果，输出 `valid: bool`（及可选理由）。与论文附录 B.2 一致：Execution → Verification → valid=True 才存入 H 并进入下一子问题。

### 附录 B.2 风格的单条路径示例（对应一条 rollout 的扩展链）

- **子问题 q1**：生成 sql1 → 执行 → 验证 → valid=True → 存入 H，进入 q2。
- **子问题 q2**：Attempt 1 执行结果与预期不符（如用了 >= 而非 >）→ valid=False → Attempt 2 修正 → 执行 → valid=True → 存入 H。
- 依此类推，直到所有 q_i 都 valid，再 **Compose final SQL**：用 M(Q, H) 把 H 中的 CTE 链组合成 WITH ... SELECT ...（与现有 CompleteSQLGenerator 一致）。

## 与 mcts_v1 的差异概览

| 环节 | mcts_v1 | mcts_v4（目标） |
|------|--------|-----------------|
| 深度 | 固定 `max_depth`（如 8） | 由**问题拆分数量**决定（初始 LLM 输出子问题个数） |
| 节点语义 | 每层 = 一步 CTE | 每个节点 = **一个子问题** |
| 节点内 | 生成一批 CTE 变体 → 执行分桶 → 选一个扩展 | 生成 CTE → **check**「当前 CTE 能否解决本子问题」→ yes 则节点完成，no 则**迭代修改**（最多 3 次） |
| 扩展 / 选择 / 模拟 / 回传 | 按执行结果分桶建子节点，UCB 选下一节点；叶节点生成多 SQL 变体→执行分桶得 reward→回传 | 扩展与选择**同 v1**（分桶建子节点 + UCB）；**模拟**在叶/路径完整时以路径为前缀生成完整 SQL→执行→分桶得 reward；**回传**同 v1 沿路径更新 |
| 终止 | 到叶或 max_depth | 所有**子问题**都解决完后，在**最终路径**上生成完整 SQL 并统计 |

## 流程简述

1. **初始化**
   - 用 LLM 对原始 question 做**问题拆分**，得到子问题列表 `[sub_q1, sub_q2, ...]`。
   - `max_depth = len(子问题)`（或 `min(该值, 原 max_depth)` 做上界）。

2. **每个节点 = 一个子问题**
   - 节点上带当前子问题 `sub_qi`（及前序 CTE 链、schema 等）。
   - 生成当前子问题对应的 CTE。
   - **Check**：LLM 判断「当前 CTE 结果是否已经能解决本子问题」：
     - **yes** → 本节点标记完成，可参与扩展/回溯；
     - **no** → 在同一节点内**迭代修改 CTE**，最多 3 次；若 3 次后仍 no，可标记为「未完成」或建失败子节点（与 v1 失败节点类似）。

3. **扩展与选择（与 v1 一致，并保留随机选）**
   - 按**执行结果分桶**建子节点（同一子问题下多次尝试可按结果分桶，形成多个子节点）。
   - **模拟（Simulation）**：扩展出多个子节点后，沿路径继续时需**随机选一个**子节点往下（与 Alpha-SQL 一致），而非仅用 UCB；在到达叶节点（或当前路径已覆盖所有子问题）时，以**该路径为前缀**用 CompleteSQLGenerator 生成**完整 SQL**，执行得到结果，按 v1 的方式做自一致性分桶得到 reward（即「按子节点作为前缀生成完整 SQL → 执行 → 据此回传」）。
   - **回传（Backpropagation）**：与 v1、Alpha-SQL 一致：**整条路径上的节点都会得到同一个 reward**。即在叶/终节点算出一个 reward 后，从该节点一路回溯到根，每个路径上的节点都做 `N += 1`、`Q += reward`（或等价的 visit_count / total_reward 更新）。这样 UCB 里每个节点的 Q/N 才反映「经过该节点的路径」的平均回报。参见 Alpha-SQL-2.2.4 `alphasql/algorithm/mcts/mcts.py` 的 `backpropagate()`。
   - **Selection（选下一轮起点）**：用 **UCB** 选择下一轮的起始节点（从根开始选到叶，此处用 UCB；模拟阶段沿路径往下则用**随机选**，见上）。
   - 如此扩展直到「所有子问题都解决」的路径出现。

4. **收尾**
   - 在**最终路径**上生成完整 SQL，统计收集（与 v1 的 rollout 统计方式兼容即可）。

---

## mcts_v4 运行逻辑（代码层面）

当 `use_decompose_flow=True`（如 `run_v4_arcwise_alpha_sql.sh` 传入 `--use_decompose_flow`）时，实际执行的是 v4 分支，入口与单步如下。

### 入口与开关

- **入口**：`MCTSWorkflow.solve(question, schema_info, additional_context, ...)`（`mcts_workflow.py`）。
- **开关**：构造 workflow 时 `use_decompose_flow=True`；在 `solve()` 内若为 True，每次 rollout 调用 `_execute_mcts_rollout_v4()`，否则调用 `_execute_mcts_rollout()`（v1 逻辑）。

### 初始化（仅 v4）

1. 若 `use_decompose_flow and question_decomposer`：
   - 调用 `QuestionDecomposer.decompose(question, schema_info, additional_context)` 得到 `sub_questions`。
   - 挂到根节点：`root_node.sub_questions = sub_questions`，`root_node.sub_question_index = -1`。
   - `max_depth = min(len(sub_questions), 原 max_depth)`，即深度由子问题个数决定。

### 单次 Rollout（v4）：Select → Expand → Simulate → Reward → Backprop

一次 rollout 由 `_execute_mcts_rollout_v4()` 完成，顺序如下：

| 步骤 | 方法 | 含义 |
|------|------|------|
| 1. **Select** | `_select_to_leaf_v4()` | 从根出发，按 UCB1 选子节点；若有未访问子节点则优先选未访问的，直到到达当前树的**叶节点**（无子或 terminal）。返回从根到该叶的 **path**。 |
| 2. **终态判断** | `_is_terminal_v4(path)` | 若 path 已覆盖所有子问题（每个子问题都有「已解决」的节点）或当前叶已是 terminal，则视为可生成完整 SQL 的终态。 |
| 3a. **终态** | — | 直接对 path 调用 `_reward_from_path_v4(path)` 得到 reward 和 selected_sql，再 `_mcts_backpropagation(path, reward)`，本 rollout 结束。 |
| 3b. **非终态** | `_expand_leaf_v4(leaf)` | 在叶节点上**扩展**：对当前子问题生成 CTE → 执行 → **M_verify**（`CTESufficientChecker.verify`）→ 通过则按执行结果分桶建子节点；不通过则同一节点内迭代（最多 3 次），把失败信息传给下一轮 CTE 生成。 |
| 4. **Simulate** | `_simulate_v4(child)` | 从扩展出的子节点中 **random 选一个** child，再从该 child 起反复「若未扩展则 `_expand_leaf_v4`，再 random 选一个子节点」，直到到达 terminal 或覆盖所有子问题，得到一条从根到终节点的 **sim_path**。 |
| 5. **Reward** | `_reward_from_path_v4(sim_path)` | 用 sim_path 上的 CTE 链，由 `CompleteSQLGenerator` 生成多条完整 SQL 变体 → 执行 → 按结果分桶，reward = 最大桶计数 / 变体数，并得到 selected_sql 与统计信息。 |
| 6. **Backprop** | `_mcts_backpropagation(sim_path, reward)` | 沿 sim_path 从叶回溯到根，对路径上每个节点更新 `visit_count += 1`、`backup_reward_sum += reward`（以及 total_reward / average_reward），供后续 UCB 使用。 |

### 节点内扩展（v4）：`_expand_leaf_v4(leaf)`

- 取当前子问题：`sub_questions[leaf.sub_question_index]`，以及从根到 leaf 的**前缀执行历史** `h_prefix`（已解决子问题的 q、cte、result_summary）。
- 最多 **3 轮**迭代：
  - 用 `_generate_cte_variants(leaf, failed_attempts_v4=...)` 生成当前子问题的 CTE 变体（首轮 `failed_attempts_v4=[]`，之后把 M_verify 不通过时的 cte+reason 传入）。
  - 执行、去重后，对每个 CTE 调用 `CTESufficientChecker.verify(原始问题, 当前子问题, h_prefix, 当前 CTE, 执行结果)`。
  - 若有 **valid=True**，则把这些结果按执行结果签名分桶，为每个桶创建一个子节点（子节点带 `sub_question_index+1`、下一子问题、cte、execution_results），打乱子节点顺序，标记 `leaf.is_expanded = True`，结束扩展。
- 若 3 轮后仍无 valid，但存在执行成功的 CTE，则兜底接受一个桶，避免无子节点。

### 多 Rollout 与最终输出

- `solve()` 内按 `rollouts_per_iteration` 执行多次 `_execute_mcts_rollout_v4()`，得到多条 rollout 的 reward、selected_sql、all_sql_variants 等。
- 最优 SQL：`SQLSelector.select_by_highest_reward(rollout_stats_list)`（reward 最高的一条的 selected_sql）。
- 返回结果中包含 `optimal_sql`、`rollout_stats`、`all_sqls_with_attributes`（供 Hit@1 / 任一路径对评估）。

### 与 v1 的差异（简要）

- **深度**：v4 由子问题个数决定；v1 固定 `max_depth`。
- **节点语义**：v4 每节点对应一个子问题，扩展时先 M_verify 再分桶建子；v1 每层为一步 CTE，直接按执行结果分桶扩展。
- **Select/Simulate**：v4 与 v1 均为「Select 用 UCB 到叶」；v4 的 Simulate 从扩展出的子中 **random 选**（与 Alpha-SQL 一致），v1 也有按桶权重随机选。
- **Reward/Backprop**：v4 与 v1 均为「路径末端生成多 SQL 变体 → 执行分桶 → reward 回传整条路径」。

---

## 建议实现步骤（在 mcts_v4 内改）

### 1. 问题拆分模块（新增）

- **文件**：例如 `agents/question_decomposer.py`。
- **输入**：`question`, `schema_info`, `additional_context`。
- **输出**：`List[str]` 子问题，例如 `["子问题1", "子问题2", ...]`。
- **用法**：在 `MCTSWorkflow.solve()` 开头调用一次，得到 `sub_questions`，并设 `self.max_depth = min(len(sub_questions), 原max_depth)`。

### 2. 节点与树对「子问题」的支持

- **MCTSNode**：
  - 增加 `sub_question_index: int`、`sub_question: str`（当前节点对应的子问题）；
  - 根节点可 `sub_question_index=-1` 表示「整题」，或第 0 个子问题由根代表，依你约定。
- **MCTSTree / 扩展逻辑**：
  - 扩展时，子节点对应「下一个子问题」或「同一子问题的不同执行桶」（与 v1 一致：按执行结果分桶建子节点）；
  - 若采用「每个节点明确对应一个 sub_question」，则同一层/同一父下可有多子节点（不同桶），但 `sub_question_index` 递增或与父一致（由你定义层级语义）。

### 3. 节点内 CTE + M_verify 迭代（核心改动，对应 Algorithm 2）

- **位置**：在 `_mcts_expansion()` 中，对「当前节点」在生成 CTE 后，不直接分桶扩展，而是先：
  1. **Generate**：生成当前子问题 q_i 的 CTE（可复用 `CTEGenerator`，prompt 里传入 `sub_question`、前序执行历史 H）；
  2. **Execute**：执行该 CTE（探针或完整执行，与 v1 一致），得到 r_i；
  3. **Verify**：**M_verify(Q, q_i, H_prefix, cte_i, r_i)** —— 需要**原始问题 Q** 和**前缀子问题/执行历史 H_prefix**（即已解决的 q_1..q_{i-1} 及其 cte、结果），以便模型在「整题目标 + 已做过的子问题」上下文中判断当前子问题的 CTE 是否足够。输入：原始问题、当前子问题、前缀 (q, cte, r)、当前 CTE、当前执行结果；输出 `valid: bool`（及可选理由）；
  4. 若 **valid=True**：将 (q_i, cte_i, r_i, valid) 视为该节点的确定结果并加入路径（执行历史 H），再按 v1 做**执行结果分桶、建子节点**；
  5. 若 **valid=False**：在同一节点内**迭代**（最多 3 次）：把「当前 CTE + 执行结果 + 模型说的不足」反馈给 CTE 生成器，生成新 CTE，回到步骤 2；超过 3 次则按「未完成/失败」处理（与 v1 失败节点类似）。

- **新增 Agent**：例如 `agents/cte_sufficient_checker.py`（或 `subquery_verifier`），实现 **M_verify**：输入 (Q, q_i, H_prefix, sql_i, r_i)，输出 valid 及可选理由。其中 Q=原始问题，H_prefix=路径上当前节点之前的 (q, cte, r) 列表。

### 4. 深度与终止

- 深度：`max_depth = len(sub_questions)`（或再 cap）。
- 终止条件：路径上已经覆盖并解决了所有子问题（每个 `sub_question_index` 都有「已解决」的节点），则在该路径上生成完整 SQL；多 rollout 仍按 v1 方式跑，最后在最终路径上收集 SQL 与统计。

### 5. 完整 SQL 生成与统计（对应 sql_final ← M(Q, H)）

- 在选定的**最终路径**上得到执行历史 H = [(q1, cte1, r1), (q2, cte2, r2), ...]（所有 valid=True）。
- 用现有 `CompleteSQLGenerator` 根据**整题 Q + H**（即整题 + 路径上的 CTE 链）生成完整 SQL，即论文中的 **Compose final SQL: sql_final ← M(Q, H)**（附录 B.2 的 WITH ... 组合）。
- 统计字段可与 v1 保持一致，便于和 mcts_v1 的 rollout 结果对比（如 `rollout_stats`、`all_sqls_with_attributes`）。

## 文件改动清单（建议）

- **新增**
  - `agents/question_decomposer.py`：问题拆分，对应 Algorithm 2 的 Decompose Q into SQ。
  - `agents/cte_sufficient_checker.py`（或 `subquery_verifier.py`）：**M_verify**，输入 (Q, q_i, H_prefix, sql_i, r_i)，节点内「当前 CTE/SQL 能否解决本子问题」的 valid 判断。
- **修改**
  - `core/mcts_node.py`：增加 `sub_question_index`、`sub_question`（及可选「节点是否已完成」标记）。
  - `mcts_workflow.py`：
    - `solve()` 开头：调用 decomposer，设 `max_depth`，把子问题列表挂到根或全局；
    - `_mcts_expansion()`：在生成 CTE 后接入「Check → 迭代最多 3 次」逻辑，再执行结果分桶、建子节点；
    - 扩展/选择/回溯逻辑与 v1 保持一致，仅节点语义和深度来源不同。
- **可选**
  - `agents/cte_generator.py`：若支持「针对单个子问题 + 前序 CTE 链」的生成，可加一个接口或重载，便于在节点内迭代时传入「上一轮 CTE + 不足原因」。

## 效果对比

- 保留 `mcts_v1` 的 test/ 与评估脚本，对同一批 question 同时跑 v1 和 v4，比较：
  - 执行准确率、推理步数、耗时、节点数等。
- 本 README 仅作设计与实现要点，具体类名、函数签名以实际代码为准。
