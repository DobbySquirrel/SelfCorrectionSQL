#!/bin/bash
# 测试特定例子的脚本
# 用法: bash test_specific_examples.sh

cd "$(dirname "$0")/../../.." || exit 1

# 颜色定义
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# 输出目录
OUT_DIR="workflows/mcts_v1/test/out/specific_examples"
mkdir -p "$OUT_DIR"

# 基础参数
PPL_FILE="data/subset_ppl_dev_python.json"
GOLD_FILE="data/sub_sampled_bird_dev_set.json"
BASE_URL="http://localhost:8000/v1"

echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}测试特定例子${NC}"
echo -e "${BLUE}========================================${NC}"

# 测试 1: 1158 - Force_S1
echo -e "\n${GREEN}[测试 1/4] Question 1158 - Force_S1${NC}"
echo "问题: List all patients who were born in 1937 whose total cholesterol was beyond the normal range."
python workflows/mcts_v1/test/test_mcts.py \
   --ppl_file "$PPL_FILE" \
   --qid "1158" \
   --sql_out "$OUT_DIR/1158_force_s1_sql.txt" \
   --json_out "$OUT_DIR/1158_force_s1_result.json" \
   --gold_file "$GOLD_FILE" \
   --max_workers 1 \
   --parallel_workers 5 \
   --strategy_mode FORCE_S1 \
   --multi_base_urls "$BASE_URL"

# 测试 2: 1195 - Force_S2
echo -e "\n${GREEN}[测试 2/4] Question 1195 - Force_S2${NC}"
echo "问题: What is the average blood albumin level for female patients with a PLT greater than 400 who have been diagnosed with SLE?"
python workflows/mcts_v1/test/test_mcts.py \
   --ppl_file "$PPL_FILE" \
   --qid "1195" \
   --sql_out "$OUT_DIR/1195_force_s2_sql.txt" \
   --json_out "$OUT_DIR/1195_force_s2_result.json" \
   --gold_file "$GOLD_FILE" \
   --max_workers 1 \
   --parallel_workers 5 \
   --strategy_mode FORCE_S2 \
   --multi_base_urls "$BASE_URL"

# 测试 3: 690 - Force_S3
echo -e "\n${GREEN}[测试 3/4] Question 690 - Force_S3${NC}"
echo "问题: Identify the latest badge awarded to the user with the display name Emmett."
python workflows/mcts_v1/test/test_mcts.py \
   --ppl_file "$PPL_FILE" \
   --qid "690" \
   --sql_out "$OUT_DIR/690_force_s3_sql.txt" \
   --json_out "$OUT_DIR/690_force_s3_result.json" \
   --gold_file "$GOLD_FILE" \
   --max_workers 1 \
   --parallel_workers 5 \
   --strategy_mode FORCE_S3 \
   --multi_base_urls "$BASE_URL"

# 测试 4: 1104 - LLM_PICK_ONCE
echo -e "\n${GREEN}[测试 4/4] Question 1104 - LLM_PICK_ONCE${NC}"
echo "问题: What was the potiential for Francesco Parravicini on 2010/8/30?"
python workflows/mcts_v1/test/test_mcts.py \
   --ppl_file "$PPL_FILE" \
   --qid "1104" \
   --sql_out "$OUT_DIR/1104_llm_pick_once_sql.txt" \
   --json_out "$OUT_DIR/1104_llm_pick_once_result.json" \
   --gold_file "$GOLD_FILE" \
   --max_workers 1 \
   --parallel_workers 5 \
   --strategy_mode LLM_PICK_ONCE \
   --multi_base_urls "$BASE_URL"

echo -e "\n${BLUE}========================================${NC}"
echo -e "${BLUE}所有测试完成！${NC}"
echo -e "${BLUE}结果保存在: $OUT_DIR${NC}"
echo -e "${BLUE}========================================${NC}"

