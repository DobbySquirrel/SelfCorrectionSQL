#!/usr/bin/env bash
# 在策略 FORCE_S2 下跑「S1/S2/S7 都错但 mcts_v3 对」的 29 个候选样本（用于排查框架/连接类 bug）
# 使用 arcwise 数据与 gold，结果写入 out/bug_candidates_FORCE_S2_result.json

set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
cd "$REPO_ROOT"

PPL_FILE="$SCRIPT_DIR/../../mcts_v3/data/arcwise_plat_sql_only_with_diff_withSchema.json"
GOLD_FILE="$PPL_FILE"
QIDS="27,41,62,77,95,159,412,427,465,480,533,557,584,587,736,892,901,915,1080,1114,1157,1187,1247,1256,1270,1357,1389,1472,1529"

export PYTHONPATH="$REPO_ROOT:$PYTHONPATH"

# 若未设置 DB_ROOT_DIR，脚本会根据 ppl 格式自动从 mcts_v3 config 读取 bird_db_root；也可手动指定：
# export DB_ROOT_DIR=/path/to/bird/root
# 或加参数: --db_root /path/to/bird/root

python "$SCRIPT_DIR/test_mcts.py" \
  --ppl_file "$PPL_FILE" \
  --gold_file "$GOLD_FILE" \
  --qids "$QIDS" \
  --strategy_mode FORCE_S2 \
  --json_out "$SCRIPT_DIR/out/bug_candidates_FORCE_S2_result.json" \
  --sql_out "$SCRIPT_DIR/out/bug_candidates_FORCE_S2_sql.txt" \
  --parallel_workers 5 \
  --max_workers 1 \
  --rollouts_per_iteration 8 \
  --task_timeout 1800 \
  "$@"

echo "Done. JSON: $SCRIPT_DIR/out/bug_candidates_FORCE_S2_result.json"
