# NL2SQL Multi-Agent System

这是一个基于AutoGen的多智能体NL2SQL系统，支持多种问题解决策略。

## 📁 项目结构

### 核心模块
- `agents/` - 多智能体系统实现
- `core/` - 核心功能模块（数据库连接、推理树等）
- `workflows/` - 不同的问题解决工作流
- `utils/` - 工具函数和辅助类
- `config/` - 配置文件

### 测试模块
- `test/stage1_basic/` - 第一阶段：基础智能体测试
  - `test_agents.py` - 主要测试文件
  - `test_agents_retry.py` - 重试机制测试
  - `test_agents_retry_ongoing.py` - 断点续传测试

- `test/stage2_function_generation/` - 第二阶段：函数生成测试
  - `test_generate_function.py` - 基础函数生成
  - `test_generate_function_dev.py` - 开发版本
  - `test_generate_function_dev_7_1.py` - 特定版本测试
  - `test_generate_function_retry.py` - 重试版本
  - `test_generate_function_based_example.py` - 基于示例的生成

- `test/stage3_advanced/` - 第三阶段：高级算法测试
  - `test_agents_star.py` - A*搜索算法
  - `test_agents_star_retry.py` - A*搜索重试

### 工具模块
- `tools/analysis/` - 分析工具
  - `add_example.py` - 添加示例
  - `check_sql_count.py` - SQL计数检查
  - `compare_json_files.py` - JSON文件比较
  - `compare_results.py` - 结果比较
  - `final_sql_validator.py` - SQL验证器
  - `get_table_info.py` - 表信息获取
  - `gpt_analysis_sql.py` - GPT SQL分析
  - `merge_results.py` - 结果合并
  - `process_single_id.py` - 单ID处理
  - `rename_cache_files.py` - 缓存文件重命名
  - `result_analyzer.py` - 结果分析器

- `tools/conversion/` - 转换工具
  - `sql_to_mql_converter.py` - SQL到MQL转换器
  - `sql_to_mql_simple.py` - 简化版转换器

- `tools/utilities/` - 实用工具
  - `TestCase.py` - 测试用例

### 数据模块
- `data/` - 数据集
- `Output/` - 输出结果
- `evaluation/` - 评估模块
- `score_caluation/` - 分数计算
- `tongji/` - 统计模块

## 🚀 使用方法

### 基础测试
```bash
cd test/stage1_basic
python test_agents.py --sample  # 运行前5个样本
python test_agents.py  # 运行完整数据集
```

### 函数生成测试
```bash
cd test/stage2_function_generation
python test_generate_function.py
```

### A*搜索测试
```bash
cd test/stage3_advanced
python test_agents_star.py
```

## 🔧 配置

配置文件位于 `config/config.yaml`，包含模型配置和API设置。

## 📊 输出

所有测试结果保存在 `Output/` 目录下，按日期和版本组织。
