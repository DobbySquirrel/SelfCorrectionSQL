# A3 code diff (clarify-a0-a2c → clarify-a3-v2hash)

## 设计说明

- **CTE expansion 分桶**：`utils/cte_processor.py` 已在 A0 使用 `MCTSUtils.bucket_key_for_search(res)`，由环境变量 `MCTS_USE_SIGNATURE_V2=1` 切换 v2。
- **本次改动（4 LOC Python）**：SQL 终选分桶与 rollout reward 分桶同步走 `bucket_key_for_search`，避免 CTE 用 v2、SQL 仍用 legacy 的不一致。
- **运行方式**：`MCTS_USE_SIGNATURE_V2=1 ./scripts/run_clarify_a0_30q.sh full`（4-shard 并行 + merge）。

## Python diff（+4 / -4 LOC）

```diff
# workflows/mcts_v4/utils/mcts_helpers.py
-            key = MCTSUtils.create_result_signature(res)
+            key = MCTSUtils.bucket_key_for_search(res)

# workflows/mcts_v4/utils/sql_result_processor.py  (×3)
-                    key = MCTSUtils.create_result_signature(res)
+                    key = MCTSUtils.bucket_key_for_search(res)
-                    if MCTSUtils.create_result_signature(res) == best_key:
+                    if MCTSUtils.bucket_key_for_search(res) == best_key:
-                    sql_info['result_signature'] = MCTSUtils.create_result_signature(res)
+                    sql_info['result_signature'] = MCTSUtils.bucket_key_for_search(res)
```

## 脚本改动（`scripts/run_clarify_a0_30q.sh`）

- `MCTS_USE_SIGNATURE_V2` 可由环境变量传入（默认 0）
- 新增 `full`：设 `MCTS_USE_SIGNATURE_V2=1` → `start-sharded` → 等待 → `merge-shards`
- shard worker screen 内传递 `MCTS_USE_SIGNATURE_V2`

## 未改动的 v4 decompose 路径

`mcts_workflow.py` 中 `_expand_leaf_v4` 仍直接调用 `create_result_signature`（`use_decompose_flow=True` 时）。本任务 **standard mode** 不走该路径。

---

**状态：等人 review 后再跑 Task 7.4**
