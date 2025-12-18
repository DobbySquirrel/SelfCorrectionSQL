# Phase 1: Pre-processing 使用指南

## 🚀 快速开始

### 方法1: 使用快速启动脚本（推荐）

```bash
# 使用默认路径（test_mcts.py中的路径，已验证可用）
# 默认路径: /ssd/shenshuyu/work/bird/dev_20240627/dev_databases
bash workflows/mcts/utils/quick_start_phase1.sh

# 或指定自定义路径
export DB_ROOT_DIR=/path/to/your/databases
bash workflows/mcts/utils/quick_start_phase1.sh
```

**注意**: 默认路径已配置为 `test_mcts.py` 中使用的路径，包含20个数据库，可以直接使用。

### 方法2: 直接运行Python脚本

```bash
# 使用默认路径（test_mcts.py中的路径）
python workflows/mcts/utils/relationship_preprocessor.py \
    --output_file workflows/mcts/data/relationships.json

# 或指定自定义路径
python workflows/mcts/utils/relationship_preprocessor.py \
    --db_root_dir /path/to/databases \
    --output_file workflows/mcts/data/relationships.json
```

### 方法3: 处理特定数据库

```bash
python workflows/mcts/utils/relationship_preprocessor.py \
    --db_root_dir /path/to/databases \
    --output_file workflows/mcts/data/relationships.json \
    --db_ids database1 database2 database3
```

## 📋 参数说明

- `--db_root_dir`: 数据库根目录（可选，默认使用test_mcts.py中的路径: `/ssd/shenshuyu/work/bird/dev_20240627/dev_databases`）
- `--output_file`: 输出文件路径（默认: `workflows/mcts/data/relationships.json`）
- `--db_ids`: 要处理的数据库ID列表（可选，不指定则处理所有）
- `--skip_existing`: 跳过已存在的数据库（默认: True）

## 📁 输出格式

生成的 `relationships.json` 文件格式：

```json
{
  "database1": {
    "relationships": [
      {
        "table1": "schools",
        "col1": "CDSCode",
        "table2": "frpm",
        "col2": "CDSCode",
        "relationship_type": "1:1",
        "description": "These tables are vertically partitioned...",
        "confidence": "high"
      }
    ],
    "metadata": {
      "processed_at": "2024-12-XX XX:XX:XX",
      "total_relationships": 15,
      "total_tables": 5
    }
  }
}
```

## ✅ 验证输出

运行验证脚本：

```bash
python -c "
import json
with open('workflows/mcts/data/relationships.json') as f:
    data = json.load(f)
    print(f'处理了 {len(data)} 个数据库')
    for db_id, info in list(data.items())[:5]:
        print(f'{db_id}: {len(info[\"relationships\"])} 个关系')
"
```

## 🔧 测试关系格式化工具

```bash
python workflows/mcts/utils/relationship_formatter.py
```

## 📊 预期结果

- **处理时间**: 每个数据库约1-5分钟（取决于表数量和复杂度）
- **关系数量**: 每个数据库通常有5-20个关系
- **1:1关系**: 应该能识别出大部分垂直分表关系

## ⚠️ 注意事项

1. **首次运行**: 可能需要几小时处理所有数据库，建议使用 `--skip_existing` 支持断点续传
2. **内存使用**: 每个数据库处理时会在内存中加载表信息，确保有足够内存
3. **错误处理**: 如果某个数据库处理失败，会记录错误信息但继续处理其他数据库

## 🐛 故障排除

### 问题1: 找不到数据库目录

```
❌ 错误: 数据库目录不存在
```

**解决**: 检查 `--db_root_dir` 路径是否正确

### 问题2: 导入错误

```
ModuleNotFoundError: No module named 'core.database_connector'
```

**解决**: 确保在项目根目录运行脚本，或设置正确的 PYTHONPATH

### 问题3: 数据库连接失败

```
⚠️ 处理数据库 XXX 失败: ...
```

**解决**: 
- 检查数据库文件是否存在且可读
- 检查数据库文件是否损坏
- 查看详细错误信息

## 📈 下一步

完成 Phase 1 后，继续：

1. **Phase 2**: 集成关系信息到 CTE 生成器
2. **Phase 3**: 添加基数一致性验证

详见 `RELATIONSHIP_AWARE_IMPLEMENTATION_PLAN.md`

