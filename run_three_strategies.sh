#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="/hpc2hdd/home/sshen190/wtao565/SelfCorrectionSQL"
PPL_FILE="data/ppl_dev.json"
GOLD_FILE="data/dev.json"
OUT_DIR="workflows/mcts_v1/test/out"
PARALLEL_WORKERS=5
MAX_WORKERS=20
MULTI_BASE_URLS="http://localhost:8000/v1"

cd "$ROOT_DIR"
mkdir -p "$OUT_DIR"

# 这里的数字表示“已经跑完的条数（按 ppl_file 的顺序）”
# 例如：第851条是最后一个有输出的结果、而第852条没有输出 => 已完成条数=851，应从第852条继续
S1_COMPLETED_COUNT=851
S2_COMPLETED_COUNT=807

filter_result_json_keep_prefix_by_index() {
  local result_json="$1"
  local keep_prefix_n="$2"
  local ppl_file="$3"

  if [[ ! -f "$result_json" ]]; then
    echo "[WARN] 未找到 $result_json，无法做断点过滤；将从头开始（不建议）。"
    return 0
  fi

  cp -f "$result_json" "${result_json}.bak.$(date +%Y%m%d_%H%M%S)"

  python - <<'PY' "$ppl_file" "$result_json" "$keep_prefix_n"
import json
import sys
from pathlib import Path

ppl_path = Path(sys.argv[1])
res_path = Path(sys.argv[2])
keep_n = int(sys.argv[3])

ppls = json.loads(ppl_path.read_text(encoding="utf-8"))
keep_qids = set()
for i in range(min(keep_n, len(ppls))):
    qid = ppls[i].get("question_id", i)
    keep_qids.add(str(qid))

res = json.loads(res_path.read_text(encoding="utf-8"))
filtered = {k: v for k, v in res.items() if k in keep_qids}

res_path.write_text(json.dumps(filtered, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"[OK] {res_path}: keep_prefix_n={keep_n}, kept={len(filtered)}/{len(res)}")
PY
}

run_strategy_resume_from_completed_count() {
  local strat="$1"
  local completed_count="$2"
  local sql_out="$OUT_DIR/dev_${strat}_sql.txt"
  local json_out="$OUT_DIR/dev_${strat}_result.json"
  local log_out="$OUT_DIR/dev_${strat}.log"

  echo "[RUN] ${strat} 断点续跑：已完成=${completed_count}，将从第 $((completed_count+1)) 条继续（通过保留前 ${completed_count} 条结果并 --skip_processed 实现）"
  filter_result_json_keep_prefix_by_index "$json_out" "$completed_count" "$PPL_FILE"

  nohup python workflows/mcts_v1/test/test_mcts.py \
    --ppl_file "$PPL_FILE" \
    --sql_out "$sql_out" \
    --json_out "$json_out" \
    --gold_file "$GOLD_FILE" \
    --parallel_workers "$PARALLEL_WORKERS" \
    --max_workers "$MAX_WORKERS" \
    --strategy_mode "$strat" \
    --multi_base_urls "$MULTI_BASE_URLS" \
    --skip_processed \
    > "$log_out" 2>&1 &
}

run_strategy_fresh() {
  local strat="$1"
  local sql_out="$OUT_DIR/dev_${strat}_sql.txt"
  local json_out="$OUT_DIR/dev_${strat}_result.json"
  local log_out="$OUT_DIR/dev_${strat}.log"

  echo "[RUN] ${strat} 从头跑：清理旧输出后启动"
  if [[ -f "$sql_out" ]]; then mv -f "$sql_out" "${sql_out}.bak.$(date +%Y%m%d_%H%M%S)"; fi
  if [[ -f "$json_out" ]]; then mv -f "$json_out" "${json_out}.bak.$(date +%Y%m%d_%H%M%S)"; fi
  if [[ -f "$log_out" ]]; then mv -f "$log_out" "${log_out}.bak.$(date +%Y%m%d_%H%M%S)"; fi

  nohup python workflows/mcts_v1/test/test_mcts.py \
    --ppl_file "$PPL_FILE" \
    --sql_out "$sql_out" \
    --json_out "$json_out" \
    --gold_file "$GOLD_FILE" \
    --parallel_workers "$PARALLEL_WORKERS" \
    --max_workers "$MAX_WORKERS" \
    --strategy_mode "$strat" \
    --multi_base_urls "$MULTI_BASE_URLS" \
    > "$log_out" 2>&1 &
}

run_strategy_resume_from_completed_count "FORCE_S1" "$S1_COMPLETED_COUNT"
run_strategy_resume_from_completed_count "FORCE_S2" "$S2_COMPLETED_COUNT"
run_strategy_fresh "FORCE_S7"

echo "已在后台启动：FORCE_S1(断点) / FORCE_S2(断点) / FORCE_S7(从头)"
