# Data Inventory (Task 1)

Generated: 2026-06-03T13:11:06

## Primary result JSONs

| Label | Path | n | r | Hash | Model | mtime | rollout_stats | cte_buckets | timing in stats |
|---|---|---:|---:|---|---|---|---|---|---|
| A0 30q r=8 legacy | `workflows/mcts_v4/test/out/clarify_a0_a2_coder/v4_a0_30q_coder_rollouts8.json` | 30 | 8 | legacy | Qwen3-Coder-30B | 2026-06-01T16:16:21 | yes (8/q) | yes | `cte_gen_s, db_exec_s, rollout_count, rollout_s, sql_gen_s, total_s` |
  <!-- rollout_stats timing: **no per-rollout time fields** -->
| A3 30q r=8 v2 | `workflows/mcts_v4/test/out/clarify_a0_a2_coder/v4_a3_30q_coder_rollouts8.json` | 30 | 8 | v2 | Qwen3-Coder-30B | 2026-06-01T17:40:57 | yes (8/q) | yes | `cte_gen_s, db_exec_s, rollout_count, rollout_s, sql_gen_s, total_s` |
  <!-- rollout_stats timing: **no per-rollout time fields** -->
| A0 30q r=20 legacy | `workflows/mcts_v4/test/out/clarify_a0_a2_coder/v4_a0_30q_coder_rollouts20.json` | 30 | 20 | legacy | Qwen3-Coder-30B | 2026-06-01T16:51:30 | yes (20/q) | yes | `cte_gen_s, db_exec_s, rollout_count, rollout_s, sql_gen_s, total_s` |
  <!-- rollout_stats timing: **no per-rollout time fields** -->
| Final 498q r=8 v2 | `workflows/mcts_v4/test/out/clarify_a0_a2_coder/v4_final_498q_coder_rollouts8.json` | 498 | 8 | v2 | Qwen3-Coder-30B | 2026-06-01T23:39:58 | yes (8/q) | yes | `cte_gen_s, db_exec_s, rollout_count, rollout_s, sql_gen_s, total_s` |
  <!-- rollout_stats timing: **no per-rollout time fields** -->
| Baseline 498q r=20 legacy | `workflows/mcts_v4/test/out/v4_arcwise_full_result_rollouts_20.json` | 498 | 20 | legacy | Qwen3-32B (inferred) | 2026-03-14T18:04:21 | yes (20/q) | no | `cte_gen_s, db_exec_s, rollout_count, rollout_s, sql_gen_s, total_s` |
  <!-- rollout_stats timing: **no per-rollout time fields** -->
| Baseline 498q r=8 legacy (alt) | `workflows/mcts_v4/test/out/v4_arcwise_full_result.json` | 498 | 8 | legacy | Qwen3-32B (inferred) | 2026-03-13T18:56:32 | yes (8/q) | no | `cte_gen_s, db_exec_s, rollout_count, rollout_s, sql_gen_s, total_s` |
  <!-- rollout_stats timing: **no per-rollout time fields** -->
| Qwen A0 30q r=8 legacy | `workflows/mcts_v4/test/out/clarify_a0_a2_qwen32/v4_a0_30q_rollouts8.json` | 30 | 8 | legacy | Qwen3-32B | 2026-06-01T15:03:09 | yes (8/q) | yes | `cte_gen_s, db_exec_s, rollout_count, rollout_s, sql_gen_s, total_s` |
  <!-- rollout_stats timing: **no per-rollout time fields** -->

## Expected but NOT found

- **300q r=8**: NOT FOUND — user confirmed no 300-q subset exists
- **300q r=2**: NOT FOUND
- **498q r=8 legacy (Coder)**: NOT FOUND — needed for hash ablation at scale
- **30q r=2**: NOT FOUND

## Shard / auxiliary files

- 498q shard manifests: `workflows/mcts_v4/test/out/clarify_a0_a2_coder/qids_shards_498_r8/`
- 30q r=8 shards (A3): `workflows/mcts_v4/test/out/clarify_a0_a2_coder/qids_shards_r8/`
- 30q r=20 shards: `workflows/mcts_v4/test/out/clarify_a0_a2_coder/qids_shards_r20/`
- Precomputed acc: `acc_a0_cmp_r8.json`, `acc_a3_for_a3_cmp.json`, `acc_final_498q.json`
- Expansion stats: `expansion_bucket_stats_a0_r8.md`, `expansion_bucket_stats_a3_r8.md`

## Timing field availability (Task 2 prep)

- **Available (question-level)**: `stats.timing.{total_s, rollout_s, cte_gen_s, sql_gen_s, db_exec_s, rollout_count}`
- **NOT available**: `rollout_stats[].llm_total_time`, per-rollout wall time, LLM call counts, token counts
- **Fallback**: log wall-clock only; no token/GPU metrics in repo

## Items needing confirmation

- Baseline 498q r=20 legacy: model/hash inferred from context — confirm if needed
- Baseline 498q r=8 legacy (alt): model/hash inferred from context — confirm if needed
