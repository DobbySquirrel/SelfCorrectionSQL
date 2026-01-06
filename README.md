# NL2SQL System

基于 Alpha-SQL-2.2.4 的可运行 baseline，使用 Monte Carlo Tree Search (MCTS) 算法实现自然语言到 SQL 的转换。

## 📋 目录

- [环境配置](#环境配置)
- [安装依赖](#安装依赖)
- [数据集准备](#数据集准备)
- [快速开始](#快速开始)
- [MCTS 框架](#mcts-框架)
- [评估方法](#评估方法)

## 🔧 环境配置

### 数据库路径配置

项目使用 `.env` 文件管理数据库路径，避免硬编码绝对路径。

**使用方法**：

`.env` 文件已创建在项目根目录，代码会自动加载。**直接在项目根目录运行代码即可**：

```bash
cd SQL_tool_multiAgent
python your_script.py
```

代码会自动从 `.env` 文件读取 `DB_ROOT_DIR` 环境变量来查找数据库。

**修改数据库路径**：直接编辑项目根目录下的 `.env` 文件即可。

## 📦 安装依赖

```bash
pip install -r requirements.txt
```

主要依赖包：
- `pandas>=2.2.3` - 数据处理
- `numpy>=2.2.5` - 数值计算
- `openai>=2.6.1` - LLM API 客户端
- `autogen>=0.9.10` - AutoGen 框架
- `python-dotenv>=1.1.1` - 环境变量管理
- `tqdm>=4.67.1` - 进度条
- `Levenshtein>=0.27.1` - 字符串相似度计算

## 📊 数据集准备

### 下载数据集

```bash
wget https://bird-bench.oss-cn-beijing.aliyuncs.com/dev.zip
unzip dev.zip
```

### 本地模型部署

```bash
CUDA_VISIBLE_DEVICES=2,3 python -m vllm.entrypoints.openai.api_server --model /ssd/shenshuyu/Qwen3-8B  --served-model-name Qwen3-8B --max-model-len=8192 --port 8010 --host 0.0.0.0 --tensor-parallel-size 2

```

### 项目来源

本项目基于以下仓库：
```bash
git clone https://github.com/DobbySquirrel/SelfCorrectionSQL.git
```

## 🚀 快速开始

### 基本测试

```bash
# 进入项目根目录
cd SQL_tool_multiAgent

# 单样本测试
python workflows/mcts_v1/test/test_mcts.py \
  --ppl_file data/subset_ppl_dev_python.json \
  --sql_out workflows/mcts_v1/test/out/test_single.txt \
  --json_out workflows/mcts_v1/test/out/test_single.json \
  --qid 25 \
  --gold_file data/sub_sampled_bird_dev_set.json \
  --parallel_workers 5 \
  --multi_base_urls "http://localhost:8009/v1,http://localhost:8010/v1,http://localhost:8012/v1"
```

## 🎯 MCTS 框架

### MCTS 框架 V1

#### 策略模式说明

- **FORCE_S1**: 实体优先策略（自底向上）
- **FORCE_S2**: 关系优先策略（自顶向下）
- **FORCE_S3**: 主动策略（证据优先）
- **FORCE_S4**: 反应式策略（尝试-执行-修复）
- **LLM_PICK_ONCE**: LLM 自动选择策略
- **NONE**: 无策略模式

#### 使用示例

**1. 策略模式测试（无策略模式 1.6测试）**

```bash
nohup python workflows/mcts_v1/test/test_mcts.py \
   --ppl_file data/subset_ppl_dev_python.json \
   --sql_out workflows/mcts_v1/test/out/1_6_test_no_strategy_sql.txt \
   --json_out workflows/mcts_v1/test/out/1_6_test_no_strategy_result.json \
   --gold_file data/sub_sampled_bird_dev_set.json \
   --parallel_workers 5 \
   --strategy_mode NONE \
   --multi_base_urls "http://localhost:8009/v1,http://localhost:8010/v1,http://localhost:8012/v1" \
   > workflows/mcts_v1/test/out/1_6_test_no_strategy.log 2>&1 &
```

**2. 策略模式测试（LLM选择策略1.6测试）**

```bash
nohup python workflows/mcts_v1/test/test_mcts.py \
   --ppl_file data/subset_ppl_dev_python.json \
   --sql_out workflows/mcts_v1/test/out/1_6_test_with_strategy_sql.txt \
   --json_out workflows/mcts_v1/test/out/1_6_test_with_strategy_result.json \
   --gold_file data/sub_sampled_bird_dev_set.json \
   --parallel_workers 5 \
   --strategy_mode LLM_PICK_ONCE \
   --multi_base_urls "http://localhost:8009/v1,http://localhost:8010/v1,http://localhost:8012/v1" \
   > workflows/mcts_v1/test/out/1_6_test_with_strategy.log 2>&1 &
```

**3. 批量运行所有策略模式**

自动运行所有 5 种策略模式（FORCE_S1, FORCE_S2, FORCE_S3, FORCE_S4, LLM_PICK_ONCE），并为每种模式生成独立的输出文件：

```bash
nohup python workflows/mcts_v1/test/run_all_strategies.py \
   --ppl_file data/subset_ppl_dev_python.json \
   --sql_out workflows/mcts_v1/test/out/test_all_strategies.txt \
   --json_out workflows/mcts_v1/test/out/test_all_strategies.json \
   --gold_file data/sub_sampled_bird_dev_set.json \
   --parallel_workers 5 \
   --multi_base_urls "http://localhost:8009/v1,http://localhost:8010/v1,http://localhost:8012/v1" \
   > workflows/mcts_v1/test/out/test_all_strategies.log 2>&1 &
```

**批量运行指定策略**：

```bash
python workflows/mcts_v1/test/run_all_strategies.py \
   --ppl_file data/subset_ppl_dev_python.json \
   --sql_out workflows/mcts_v1/test/out/test_selected_strategies.txt \
   --json_out workflows/mcts_v1/test/out/test_selected_strategies.json \
   --gold_file data/sub_sampled_bird_dev_set.json \
   --strategies "FORCE_S1,FORCE_S2" \
   --parallel_workers 5
```

### MCTS 框架 V2

开发中...

## 📈 评估方法

### 计算准确率

#### 步骤 1：转换结果格式

使用 `score_caluation/txt2json.py` 将输出的 TXT 文件转换为 JSON 格式：

```bash
# 从项目根目录运行
python score_caluation/txt2json.py \
  --dev_set data/sub_sampled_bird_dev_set.json \
  --txt_sqls workflows/mcts_v1/test/out/test_single.txt \
  --output workflows/mcts_v1/test/out/test_single.json
```

**参数说明**：
- `--dev_set`: 开发集 JSON 文件路径（用于获取 question_id 顺序与 db_id）
- `--txt_sqls`: 输入的 TXT 文件（每行一个 SQL）
- `--output`: 输出的 JSON 文件路径
- `--start_idx`: （可选）从 dev_set 第几个样本开始对齐（默认 0）
- `--limit`: （可选）只处理 limit 条（默认处理全部）

#### 步骤 2：计算准确率

使用 `score_caluation/compute_intersection.py` 计算准确率：

```bash
# 从项目根目录运行
python score_caluation/compute_intersection.py \
  --straightforward_path workflows/mcts_v1/test/out/test_single.json \
  --ground_truth_path data \
  --data_mode sub_sampled_dev_gold.sql \
  --diff_json_path data/sub_sampled_bird_dev_set.json
```

**参数说明**：
- `--straightforward_path`: 预测结果 JSON 文件路径
- `--ground_truth_path`: Gold SQL 文件所在目录
- `--data_mode`: Gold SQL 文件名（如 `sub_sampled_dev_gold.sql`）
- `--db_root_path`: （可选）数据库根路径，如果不提供则从环境变量 `DB_ROOT_DIR` 读取
- `--diff_json_path`: 难度分类 JSON 文件路径

**完整示例**：

```bash
# 1. 转换 TXT 为 JSON
python score_caluation/txt2json.py \
  --dev_set data/sub_sampled_bird_dev_set.json \
  --txt_sqls workflows/mcts_v1/test/out/1_6_test_no_strategy_sql.txt \
  --output workflows/mcts_v1/test/out/1_6_test_no_strategy_sql.json

# 2. 计算准确率
python score_caluation/compute_intersection.py \
  --straightforward_path workflows/mcts_v1/test/out/1_6_test_no_strategy_sql.json \
  --ground_truth_path data \
  --data_mode sub_sampled_dev_gold.sql \
  --diff_json_path data/sub_sampled_bird_dev_set.json
```

**输出结果**：
- 会在预测结果文件所在目录生成 `error_analysis_*.json` 文件
- 控制台会显示总体准确率和按难度分类的准确率统计

## 📝 参数说明

### test_mcts.py 主要参数

- `--ppl_file`: 样本文件路径（JSON 数组）
- `--sql_out`: SQL 输出文件路径（TXT）
- `--json_out`: 结果输出文件路径（JSON）
- `--qid`: 按 question_id 精确定位并只跑该条
- `--qids`: 多个 question_id，用逗号分隔
- `--gold_file`: Gold SQL 文件路径（用于验证）
- `--parallel_workers`: MCTS 内部并行工作线程数（默认 5）
- `--max_workers`: 并行处理多个问题的工作线程数（默认 1）
- `--multi_base_urls`: 多个模型端点 URL，用逗号分隔
- `--strategy_mode`: 策略模式（FORCE_S1/S2/S3/S4, NONE, LLM_PICK_ONCE）
- `--max_cte_nodes`: 每次扩展节点时生成的 CTE 变体数量（默认 15）

## 🔗 相关链接

- [Alpha-SQL 原始项目](https://github.com/DobbySquirrel/SelfCorrectionSQL)
- [BIRD Benchmark](https://bird-bench.github.io/)
