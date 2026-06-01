#!/bin/bash

# 测试脚本：依次测试 LLM_PICK_ONCE, FORCE_S1, FORCE_S2 三种策略模式

# 基础配置
PPL_FILE="data/subset_ppl_dev_python.json"
GOLD_FILE="data/sub_sampled_bird_dev_set.json"
MAX_WORKERS=20
PARALLEL_WORKERS=5
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
echo -e "${BLUE}开始策略模式测试${NC}"
echo -e "${BLUE}========================================${NC}"
echo ""

# 测试策略列表
strategies=("LLM_PICK_ONCE" "FORCE_S1" "FORCE_S2")

# 遍历每个策略
for strategy in "${strategies[@]}"; do
    echo -e "${GREEN}========================================${NC}"
    echo -e "${GREEN}测试策略: ${strategy}${NC}"
    echo -e "${GREEN}========================================${NC}"
    echo ""
    
    # 生成输出文件名
    SQL_OUT="${OUTPUT_DIR}/1_10_test_5_strategy_${strategy}_sql.txt"
    JSON_OUT="${OUTPUT_DIR}/1_10_test_5_strategy_${strategy}_result.json"
    
    # 执行测试
    python workflows/mcts_v1/test/test_mcts.py \
        --ppl_file "$PPL_FILE" \
        --sql_out "$SQL_OUT" \
        --json_out "$JSON_OUT" \
        --gold_file "$GOLD_FILE" \
        --max_workers $MAX_WORKERS \
        --parallel_workers $PARALLEL_WORKERS \
        --strategy_mode "$strategy" \
        --multi_base_urls "$MULTI_BASE_URLS"
    
    # 检查执行结果
    if [ $? -eq 0 ]; then
        echo -e "${GREEN}✅ 策略 ${strategy} 测试完成${NC}"
        echo -e "${YELLOW}输出文件:${NC}"
        echo -e "  SQL: ${SQL_OUT}"
        echo -e "  JSON: ${JSON_OUT}"
    else
        echo -e "${YELLOW}⚠️  策略 ${strategy} 测试失败${NC}"
    fi
    
    echo ""
    echo -e "${BLUE}----------------------------------------${NC}"
    echo ""
done

echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}所有策略测试完成${NC}"
echo -e "${BLUE}========================================${NC}"
