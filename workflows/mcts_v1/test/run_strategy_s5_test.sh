#!/bin/bash

# 测试脚本：测试 FORCE_S5 策略模式（数据流流水线策略）

# 基础配置
MULTI_BASE_URLS="http://localhost:8000/v1"

# 输出目录
OUTPUT_DIR="workflows/mcts_v1/test/out"

# 创建输出目录（如果不存在）
mkdir -p "$OUTPUT_DIR"

# 颜色输出
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}开始测试 S5 策略（数据流流水线策略）${NC}"
echo -e "${BLUE}========================================${NC}"
echo ""

# 测试策略
STRATEGY="FORCE_S5"

echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}测试策略: ${STRATEGY}${NC}"
echo -e "${GREEN}========================================${NC}"
echo ""

# 生成输出文件名
SQL_OUT="${OUTPUT_DIR}/1_11_test_5_strategy_${STRATEGY}_sql.txt"
JSON_OUT="${OUTPUT_DIR}/1_11_test_5_strategy_${STRATEGY}_result.json"

# 执行测试

python workflows/mcts_v1/test/test_mcts.py \
      --ppl_file data/subset_ppl_dev_python.json \
      --sql_out "${SQL_OUT}" \
      --json_out "${JSON_OUT}" \
      --gold_file data/sub_sampled_bird_dev_set.json \
      --parallel_workers 5 \
      --max_workers 20 \
      --max_cte_nodes 5 \
      --max_depth 8 \
      --rollouts_per_iteration 1 \
      --num_sql_variants 6 \
      --strategy_mode "$STRATEGY" \
      --multi_base_urls "http://localhost:8000/v1" \
      --task_timeout 1800

# 检查执行结果
if [ $? -eq 0 ]; then
    echo -e "${GREEN}✅ 策略 ${STRATEGY} 测试完成${NC}"
    echo -e "${YELLOW}输出文件:${NC}"
    echo -e "  SQL: ${SQL_OUT}"
    echo -e "  JSON: ${JSON_OUT}"
else
    echo -e "${YELLOW}⚠️  策略 ${STRATEGY} 测试失败${NC}"
fi

echo ""
echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}S5 策略测试完成${NC}"
echo -e "${BLUE}========================================${NC}"
