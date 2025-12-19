# Alpha-SQL 快速开始指南

## 前提条件

1. 已安装 Python 依赖（`pip install -r requirements.txt`）
2. 已配置 LLM API（OpenAI 或其他）
**使用本地 vLLM 服务：**
```bash

# 或者使用 VLLM_API_URL 和 VLLM_API_KEY（会自动映射）
export VLLM_API_URL="http://localhost:8008/v1"
export VLLM_API_KEY="dummy-key"
```

**使用 OpenAI API：**
```bash
export OPENAI_API_KEY="your-openai-key"
```
3. 数据文件格式为 JSON，包含 `question_id`, `db_id`, `question`, `evidence`, `SQL` 等字段
4. 数据库文件按以下结构组织：
   ```
   database_root_dir/
     └── db_id/
         └── db_id.sqlite
   ```

## 快速开始（3步）

### 步骤 1: 转换数据格式

如果您的数据格式与 Alpha-SQL 不完全一致，先转换：

```bash
cd Alpha-SQL-2.2.4
python convert_data_format.py \
    --input_file /data/dev.json \
    --output_file data/dev_alpha_sql.json \
    --db_path_prefix /ssd/shenshuyu/work/bird/dev_20240627/dev_databases
```

### 步骤 2: 预处理数据

```bash
python -m alphasql.runner.preprocessor \
    --data_file_path data/dev_alpha_sql.json \
    --database_root_dir /ssd/shenshuyu/work/bird/dev_20240627/dev_databases \
    --save_root_dir data/preprocessed/dev \
    --lsh_threshold 0.5 \
    --lsh_signature_size 128 \
    --lsh_n_gram 3 \
    --lsh_top_k 20 \
    --edit_similarity_threshold 0.3 \
    --embedding_similarity_threshold 0.6 \
    --n_parallel_processes 8 \
    --max_dataset_samples -1
```


## 一键运行（使用脚本）

编辑 `run_mcts_only.sh` 中的配置，然后运行：

```bash
bash run_mcts_only.sh
```

## 数据格式说明

Alpha-SQL 需要的数据格式：

```json
[
  {
    "question_id": 0,
    "db_id": "california_schools",
    "question": "What is the highest eligible free rate?",
    "evidence": "Eligible free rate = Free Meal Count / Enrollment",
    "SQL": "SELECT ...",
    "difficulty": "simple"
  }
]
```

字段说明：
- `question_id`: 必需，整数
- `db_id`: 必需，字符串，对应数据库目录名
- `question`: 必需，自然语言问题
- `evidence`: 可选，证据/提示信息
- `SQL`: 可选，标准答案（用于评估）
- `difficulty`: 可选，难度级别



## 输出结果

运行完成后，结果保存在 `save_root_dir` 指定的目录中：
- 每个任务生成一个 `{question_id}.pkl` 文件
- 包含生成的 SQL 和 MCTS 搜索过程

