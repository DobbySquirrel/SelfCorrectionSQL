# Clarify A0–A3 Instrumentation

## Task 0 status

**Git working tree was NOT clean** at task start (branch `temp_new`, many unrelated modified files).
`workflows/mcts_v4/` was **untracked**. Proceeding with implementation in-place; commit only `workflows/mcts_v4/` when tree is isolated.

- Start commit (repo HEAD): `1b19eed chore(stage2): shell wrapper`
- Branch (intended): `clarify-a0-a2c`

## 30-question manifest

Source: key order of `test/out/v4_arcwise_full_result.json` (498q, **rollouts=8**, standard mode).

File: `test/out/clarify_a0_a2/qids_30_manifest.json`

```
1471, 1472, 1473, 1476, 1479, 1480, 1482, 1483, 1484, 1486,
1490, 1493, 1498, 1500, 1501, 1505, 1506, 1507, 1509, 1514,
1515, 1521, 1524, 1525, 1526, 1528, 1529, 1531, 1533, 1312
```

> If a dedicated 30q manifest exists elsewhere, confirm before final paper numbers.

## Baselines for acc sanity (Task 1.5)

| File | rollouts | Use |
|---|---|---|
| `test/out/v4_arcwise_full_result.json` | **8** | Primary Hit@1 / any_path baseline (±2pp) |
| `test/out/v4_arcwise_full_result_rollouts_20.json` | 20 | Secondary (±4pp if needed) |

## Config invariants

- `use_decompose_flow=False`
- `rollouts_per_iteration=8`
- `random_seed=20240601` (`test_mcts.py --random_seed`)
- Search bucketing: **legacy** (`create_result_signature`); v2 record-only until A3 (`MCTS_USE_SIGNATURE_V2=1`)

## Run A0 30q

```bash
cd SelfCorrectionSQL
export PYTHONPATH=$PWD:$PWD/Alpha-SQL-2.2.4
export MCTS_USE_SIGNATURE_V2=0

python workflows/mcts_v4/test/test_mcts.py \
  --ppl_file workflows/mcts_v1/test/out/arcwise_alpha_sql_ppl_rollouts_20_3_15_nigga.json \
  --gold_file workflows/mcts_v3/data/arcwise_plat_sql_only_with_diff_withSchema.json \
  --qids_file workflows/mcts_v4/test/out/clarify_a0_a2/qids_30_manifest.json \
  --json_out workflows/mcts_v4/test/out/clarify_a0_a2/v4_a0_30q_rollouts8.json \
  --rollouts_per_iteration 8 \
  --random_seed 20240601 \
  --max_workers 1
```

## Analysis pipeline (after JSON exists)

```bash
python workflows/mcts_v4/scripts/clarify_sanity_acc_a0.py ...
python workflows/mcts_v4/scripts/clarify_sanity_30v498.py ...
python workflows/mcts_v4/scripts/clarify_expansion_bucket_stats.py ...
python workflows/mcts_v4/scripts/clarify_hash_paired_diff.py ...
```

## A3 v2 search

```bash
export MCTS_USE_SIGNATURE_V2=1
# re-run 30q → v4_a3_30q_rollouts8.json
```

## Final 498q

**Do not run without extra GPUs** — notify when ready.
