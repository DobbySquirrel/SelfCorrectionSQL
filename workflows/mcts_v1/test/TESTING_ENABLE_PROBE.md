# Testing Guide: enable_probe Parameter

This guide explains how to test whether the `enable_probe` parameter in `SimpleRolloutWorkflow` makes a difference in SQL generation quality.

## What is `enable_probe`?

The `enable_probe` parameter controls whether the workflow executes a **Probe step** before generating CTEs. When enabled:
- The workflow first runs probe queries to gather information about the database (column values, distributions, join validity, etc.)
- This probe information is then used to inform CTE generation
- When disabled, the workflow skips this probing step and generates CTEs directly

## How to Test

### Step 1: Run Tests with `enable_probe=False` (Baseline)

Run the test script **without** the `--enable_probe` flag:

```bash
cd /hpc2hdd/home/sshen190/wtao565/SelfCorrectionSQL

python workflows/mcts_v1/test/test_single_mcts.py \
  --ppl_file data/subset_ppl_dev_python.json \
  --sql_out workflows/mcts_v1/test/out/test_probe_disabled_sql.txt \
  --json_out workflows/mcts_v1/test/out/test_probe_disabled_result.json \
  --gold_file data/sub_sampled_bird_dev_set.json \
  --parallel_workers 5 \
  --max_workers 1 \
  --strategy_mode NONE \
  --multi_base_urls "http://localhost:8009/v1,http://localhost:8010/v1,http://localhost:8012/v1" \
  > workflows/mcts_v1/test/out/test_probe_disabled.log 2>&1
```

**Note:** Since `enable_probe` defaults to `False`, omitting the flag is equivalent to `--enable_probe=False`.

### Step 2: Run Tests with `enable_probe=True`

Run the same test script **with** the `--enable_probe` flag:

```bash
cd /hpc2hdd/home/sshen190/wtao565/SelfCorrectionSQL

python workflows/mcts_v1/test/test_single_mcts.py \
  --ppl_file data/subset_ppl_dev_python.json \
  --sql_out workflows/mcts_v1/test/out/test_probe_enabled_sql.txt \
  --json_out workflows/mcts_v1/test/out/test_probe_enabled_result.json \
  --gold_file data/sub_sampled_bird_dev_set.json \
  --parallel_workers 5 \
  --max_workers 1 \
  --strategy_mode NONE \
  --enable_probe \
  --multi_base_urls "http://localhost:8009/v1,http://localhost:8010/v1,http://localhost:8012/v1" \
  > workflows/mcts_v1/test/out/test_probe_enabled.log 2>&1
```

### Step 3: Testing a Single Question (Quick Test)

For a quick test on a single question:

**Without probe:**
```bash
python workflows/mcts_v1/test/test_single_mcts.py \
  --ppl_file data/subset_ppl_dev_python.json \
  --qid 25 \
  --sql_out workflows/mcts_v1/test/out/test_probe_disabled_q25_sql.txt \
  --json_out workflows/mcts_v1/test/out/test_probe_disabled_q25_result.json \
  --gold_file data/sub_sampled_bird_dev_set.json \
  --parallel_workers 5 \
  --strategy_mode NONE
```

**With probe:**
```bash
python workflows/mcts_v1/test/test_single_mcts.py \
  --ppl_file data/subset_ppl_dev_python.json \
  --qid 25 \
  --sql_out workflows/mcts_v1/test/out/test_probe_enabled_q25_sql.txt \
  --json_out workflows/mcts_v1/test/out/test_probe_enabled_q25_result.json \
  --gold_file data/sub_sampled_bird_dev_set.json \
  --parallel_workers 5 \
  --strategy_mode NONE \
  --enable_probe
```

## Where to Find Results

### Output Files

1. **SQL Output Files** (`*_sql.txt`):
   - Location: `workflows/mcts_v1/test/out/`
   - Format: One SQL query per line, corresponding to each question in the input file
   - Example: `test_probe_disabled_sql.txt`, `test_probe_enabled_sql.txt`

2. **JSON Result Files** (`*_result.json`):
   - Location: `workflows/mcts_v1/test/out/`
   - Format: JSON object with question_id as keys, containing:
     - `sql`: Generated SQL query
     - `stats`: Statistics including timing, reward, bucket counts, etc.
     - `rollout_stats`: Detailed rollout statistics
   - Example: `test_probe_disabled_result.json`, `test_probe_enabled_result.json`

3. **Log Files** (`*.log`):
   - Location: `workflows/mcts_v1/test/out/`
   - Contains: Detailed execution logs, including:
     - Probe step execution (when enabled)
     - CTE generation steps
     - SQL execution results
     - Error messages
   - Example: `test_probe_disabled.log`, `test_probe_enabled.log`

### Key Files to Compare

- **SQL files**: Compare `test_probe_disabled_sql.txt` vs `test_probe_enabled_sql.txt`
- **JSON files**: Compare `test_probe_disabled_result.json` vs `test_probe_enabled_result.json`
- **Log files**: Compare `test_probe_disabled.log` vs `test_probe_enabled.log`

## How to Interpret Results

### 1. Accuracy Comparison

Use the evaluation script to compare accuracy:

```bash
cd /hpc2hdd/home/sshen190/wtao565/SelfCorrectionSQL

# Evaluate without probe
python score_caluation/evaluation.py \
  --predicted_sql workflows/mcts_v1/test/out/test_probe_disabled_sql.txt \
  --ground_truth data/sub_sampled_bird_dev_set.json \
  --mode dev \
  --output workflows/mcts_v1/test/out/test_probe_disabled_acc.json

# Evaluate with probe
python score_caluation/evaluation.py \
  --predicted_sql workflows/mcts_v1/test/out/test_probe_enabled_sql.txt \
  --ground_truth data/sub_sampled_bird_dev_set.json \
  --mode dev \
  --output workflows/mcts_v1/test/out/test_probe_enabled_acc.json
```

Compare the accuracy percentages in the output JSON files.

### 2. Key Metrics to Compare

From the JSON result files, compare:

1. **Gold Match Rate** (if gold file provided):
   - Check the final statistics printed in the log
   - Compare: `test_probe_disabled.log` vs `test_probe_enabled.log`
   - Look for: `[最终统计] Gold验证: X/Y 正确`

2. **Reward/Consistency Scores**:
   - In JSON files: `stats.reward` or `rollout_stats[0].reward`
   - Higher reward = better consistency across SQL variants

3. **SQL Bucket Count**:
   - In JSON files: `stats.sql_bucket_count` or `rollout_stats[0].sql_bucket_count`
   - Higher bucket count = more SQL variants produced the same result (better consistency)

4. **Valid Count**:
   - In JSON files: `stats.valid_count` or `rollout_stats[0].valid_count`
   - Number of SQL variants that executed successfully

5. **Timing**:
   - In JSON files: `stats.timing`
   - Compare `total_s`, `cte_gen_s`, `sql_gen_s`, `db_exec_s`
   - **Note**: When probe is enabled, probe time is included in `cte_gen_s`

### 3. Log Analysis

Check the log files for:

1. **Probe Execution** (when enabled):
   ```
   [Probe步骤] 开始执行Probe探测...
   [Probe步骤] 完成，...
   [Probe步骤] Probe结果已准备，将在CTE生成时使用
   ```

2. **Probe Skipped** (when disabled):
   ```
   [Probe步骤] Probe功能已禁用，跳过Probe探测步骤
   ```

3. **CTE Generation Quality**:
   - Compare the CTE paths generated in both runs
   - Check for differences in CTE depth, bucket counts

4. **SQL Execution Results**:
   - Compare error rates
   - Compare empty result rates

### 4. Example Comparison Script

Create a simple comparison script:

```python
import json

# Load results
with open('workflows/mcts_v1/test/out/test_probe_disabled_result.json', 'r') as f:
    disabled = json.load(f)

with open('workflows/mcts_v1/test/out/test_probe_enabled_result.json', 'r') as f:
    enabled = json.load(f)

# Compare metrics
for qid in disabled.keys():
    if qid in enabled:
        d_stats = disabled[qid].get('stats', {})
        e_stats = enabled[qid].get('stats', {})
        
        d_reward = d_stats.get('reward', 0)
        e_reward = e_stats.get('reward', 0)
        
        d_bucket = d_stats.get('sql_bucket_count', 0)
        e_bucket = e_stats.get('sql_bucket_count', 0)
        
        print(f"QID {qid}:")
        print(f"  Reward: {d_reward:.4f} (disabled) vs {e_reward:.4f} (enabled)")
        print(f"  Bucket: {d_bucket} (disabled) vs {e_bucket} (enabled)")
        print()
```

## Expected Differences

When `enable_probe=True`:

1. **Execution Time**: Should be longer (probe adds overhead)
2. **CTE Generation**: Should potentially be more informed (probe results guide CTE generation)
3. **SQL Quality**: May improve accuracy if probe information helps resolve ambiguities
4. **Log Content**: Should contain probe execution messages

## Troubleshooting

1. **If results are identical**: 
   - Check logs to confirm probe actually ran
   - Verify probe results were used in CTE generation
   - Some questions may not benefit from probing

2. **If probe fails**:
   - Check log for probe errors
   - Verify database connectivity
   - Check probe timeout settings

3. **If accuracy decreases with probe**:
   - Probe information might be misleading for some questions
   - Check probe results in logs to see what information was gathered

## Summary

To test `enable_probe`:
1. Run tests with and without `--enable_probe` flag
2. Compare results in `workflows/mcts_v1/test/out/` directory
3. Focus on: accuracy, reward scores, bucket counts, and execution times
4. Use evaluation script for formal accuracy comparison

