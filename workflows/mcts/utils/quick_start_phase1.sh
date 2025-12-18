#!/bin/bash
# Phase 1 快速启动脚本

set -e

echo "=========================================="
echo "Phase 1: Pre-processing 快速启动"
echo "=========================================="
echo ""

# 配置（使用test_mcts.py中的默认路径）
DEFAULT_DB_ROOT_DIR="/ssd/shenshuyu/work/bird/dev_20240627/dev_databases"
OUTPUT_FILE="${OUTPUT_FILE:-workflows/mcts/data/relationships.json}"

# 如果环境变量设置了但路径不存在，则使用默认路径
if [ -n "$DB_ROOT_DIR" ] && [ -d "$DB_ROOT_DIR" ]; then
    # 环境变量存在且路径有效，使用环境变量
    USE_DB_ROOT_DIR="$DB_ROOT_DIR"
elif [ -d "$DEFAULT_DB_ROOT_DIR" ]; then
    # 使用默认路径
    USE_DB_ROOT_DIR="$DEFAULT_DB_ROOT_DIR"
    if [ -n "$DB_ROOT_DIR" ]; then
        echo "⚠️  警告: 环境变量 DB_ROOT_DIR=$DB_ROOT_DIR 指向的路径不存在"
        echo "   使用默认路径: $USE_DB_ROOT_DIR"
        echo ""
    fi
else
    echo "❌ 错误: 找不到数据库目录"
    echo "   环境变量 DB_ROOT_DIR: ${DB_ROOT_DIR:-未设置}"
    echo "   默认路径: $DEFAULT_DB_ROOT_DIR"
    echo "   请设置环境变量 DB_ROOT_DIR 指向正确的数据库目录"
    echo "   例如: export DB_ROOT_DIR=/path/to/your/databases"
    exit 1
fi

DB_ROOT_DIR="$USE_DB_ROOT_DIR"

echo "配置信息:"
echo "  数据库根目录: $DB_ROOT_DIR"
echo "  输出文件: $OUTPUT_FILE"
echo ""

# 创建输出目录
mkdir -p "$(dirname "$OUTPUT_FILE")"

# 运行预处理器
echo "开始处理数据库..."
echo ""

# 运行预处理器（使用确定的路径）
python workflows/mcts/utils/relationship_preprocessor.py \
    --db_root_dir "$DB_ROOT_DIR" \
    --output_file "$OUTPUT_FILE" \
    --skip_existing

echo ""
echo "=========================================="
echo "处理完成!"
echo "=========================================="
echo ""
echo "验证输出文件:"
if [ -f "$OUTPUT_FILE" ]; then
    echo "✅ 文件已生成: $OUTPUT_FILE"
    echo ""
    echo "统计信息:"
    python -c "
import json
try:
    with open('$OUTPUT_FILE', 'r') as f:
        data = json.load(f)
    print(f'  处理了 {len(data)} 个数据库')
    total_rels = sum(len(info.get('relationships', [])) for info in data.values())
    print(f'  总共发现 {total_rels} 个关系')
    
    # 按类型统计
    by_type = {}
    for db_info in data.values():
        for rel in db_info.get('relationships', []):
            rel_type = rel.get('relationship_type', 'unknown')
            by_type[rel_type] = by_type.get(rel_type, 0) + 1
    
    print('  关系类型分布:')
    for rel_type, count in sorted(by_type.items()):
        print(f'    {rel_type}: {count}')
except Exception as e:
    print(f'  ⚠️  读取文件失败: {e}')
"
else
    echo "❌ 文件未生成"
fi

echo ""
echo "下一步:"
echo "  1. 检查输出文件是否正确"
echo "  2. 运行测试验证关系信息"
echo "  3. 继续 Phase 2: 集成到 CTE 生成器"

