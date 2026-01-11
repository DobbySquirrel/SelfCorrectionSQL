#!/bin/bash

# 测试脚本：测试指定策略模式
# 用法: ./run_strategy_s5_test.sh [策略名称]
# 示例: ./run_strategy_s5_test.sh FORCE_S5
#        ./workflows/mcts_v1/test/run_strategy_s5_test.sh FORCE_S6
#        ./run_strategy_s5_test.sh LLM_PICK_ONCE
# 可用策略: FORCE_S1, FORCE_S2, FORCE_S3, FORCE_S5, FORCE_S6, FORCE_S7, FORCE_S8, LLM_PICK_ONCE, NONE

# 颜色输出
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

# 检查是否提供了策略参数
if [ $# -eq 0 ]; then
    echo -e "${RED}错误: 请指定要测试的策略${NC}"
    echo ""
    echo "用法: $0 [策略名称]"
    echo ""
    echo "可用策略:"
    echo "  FORCE_S1      - 实体优先策略"
    echo "  FORCE_S2      - 关系优先策略"
    echo "  FORCE_S3      - 基于证据的策略"
    echo "  FORCE_S5      - 数据流流水线策略"
    echo "  FORCE_S6      - 业务规则模块策略"
    echo "  FORCE_S7      - 粒度/主键策略"
    echo "  FORCE_S8      - 对照/审计型策略"
    echo "  LLM_PICK_ONCE - LLM自动选择策略"
    echo "  NONE          - 无策略（基线）"
    echo ""
    echo "示例:"
    echo "  $0 FORCE_S5"
    echo "  $0 FORCE_S6"
    echo "  $0 LLM_PICK_ONCE"
    exit 1
fi

# 获取策略参数
STRATEGY="$1"

# 验证策略是否有效
VALID_STRATEGIES=("FORCE_S1" "FORCE_S2" "FORCE_S3" "FORCE_S5" "FORCE_S6" "FORCE_S7" "FORCE_S8" "LLM_PICK_ONCE" "NONE")
VALID=false
for valid_strategy in "${VALID_STRATEGIES[@]}"; do
    if [ "$STRATEGY" == "$valid_strategy" ]; then
        VALID=true
        break
    fi
done

if [ "$VALID" == false ]; then
    echo -e "${RED}错误: 无效的策略 '${STRATEGY}'${NC}"
    echo ""
    echo "可用策略: ${VALID_STRATEGIES[*]}"
    exit 1
fi

# 基础配置
MULTI_BASE_URLS="http://localhost:8000/v1"

# 输出目录
OUTPUT_DIR="workflows/mcts_v1/test/out"

# 创建输出目录（如果不存在）
mkdir -p "$OUTPUT_DIR"

echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}测试策略: ${STRATEGY}${NC}"
echo -e "${GREEN}========================================${NC}"
echo ""

# 生成输出文件名
SQL_OUT="${OUTPUT_DIR}/1_11_test_6_strategy_${STRATEGY}_sql.txt"
JSON_OUT="${OUTPUT_DIR}/1_11_test_6_strategy_${STRATEGY}_result.json"

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
echo -e "${BLUE}策略 ${STRATEGY} 测试完成${NC}"
echo -e "${BLUE}========================================${NC}"
