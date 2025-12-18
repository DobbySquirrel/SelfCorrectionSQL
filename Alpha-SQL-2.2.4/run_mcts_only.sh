#!/bin/bash

# 直接运行 Alpha-SQL MCTS（跳过预处理步骤）
# 
# 使用方法:
#   bash run_mcts_only.sh

set -e  # 遇到错误立即退出

# 获取脚本所在目录
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# 切换到项目根目录（Alpha-SQL-2.2.4 的父目录）
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${PROJECT_ROOT}"

echo "工作目录: ${PROJECT_ROOT}"
echo "脚本目录: ${SCRIPT_DIR}"
echo ""

# ========== 配置区域 ==========

# ========== 模型配置 ==========
# MCTS 运行阶段使用的模型（本地 vLLM 服务）
export OPENAI_API_BASE="http://localhost:8010/v1"
export OPENAI_API_KEY="dummy-key"

echo "========== MCTS 模型配置 =========="
echo "OPENAI_API_BASE: ${OPENAI_API_BASE}"
echo "OPENAI_API_KEY: ${OPENAI_API_KEY:0:20}..."
echo ""

# 数据集名称
DATASET_NAME="dev"

# 模型配置
MODEL_NAME="Qwen/Qwen3-8B"  # 或您使用的其他模型
N_PROCESSES=32  # 并行进程数

# MCTS 参数
MAX_ROLLOUT_STEPS=24
MAX_DEPTH=16
EXPLORATION_CONSTANT=1.414

# 数据库根目录
DB_ROOT_DIR="/ssd/shenshuyu/work/bird/dev_20240627/dev_databases"  # 您的数据库根目录

# 预处理后的任务文件路径（如果预处理已完成）
# 注意：preprocessor 会在 save_root_dir 下创建 data_split 子目录
# 所以实际路径是 save_root_dir/data_split/data_split/tasks.pkl
PREPROCESSED_TASKS_FILE="${SCRIPT_DIR}/data/preprocessed/${DATASET_NAME}/${DATASET_NAME}/tasks.pkl"

# 子集文件路径（用于指定要运行的 question_id）
# 如果设置为 null，则运行所有任务
# 如果指定 JSON 文件路径，则只运行该文件中 question_id 对应的任务
SUBSET_FILE_PATH="${PROJECT_ROOT}/data/dev.json"

# 检查任务文件是否存在
if [ ! -f "${PREPROCESSED_TASKS_FILE}" ]; then
    echo "❌ 错误: 任务文件不存在: ${PREPROCESSED_TASKS_FILE}"
    echo ""
    echo "请先运行预处理步骤，或检查文件路径是否正确。"
    echo ""
    echo "如果文件在其他位置，请修改脚本中的 PREPROCESSED_TASKS_FILE 变量。"
    exit 1
fi

echo "✓ 找到任务文件: ${PREPROCESSED_TASKS_FILE}"

# 检查子集文件是否存在（如果指定了）
if [ -n "${SUBSET_FILE_PATH}" ] && [ "${SUBSET_FILE_PATH}" != "null" ]; then
    if [ ! -f "${SUBSET_FILE_PATH}" ]; then
        echo "⚠️  警告: 子集文件不存在: ${SUBSET_FILE_PATH}"
        echo "将运行所有任务（不进行过滤）"
        SUBSET_FILE_PATH="null"
    else
        echo "✓ 找到子集文件: ${SUBSET_FILE_PATH}"
        echo "  将只运行子集文件中指定的 question_id"
    fi
fi
echo ""

# ========== 创建配置文件 ==========
echo "========== 创建配置文件 =========="
CONFIG_FILE="${SCRIPT_DIR}/config/${DATASET_NAME}_config.yaml"
mkdir -p "${SCRIPT_DIR}/config"

# 根据 SUBSET_FILE_PATH 的值决定 YAML 格式
if [ "${SUBSET_FILE_PATH}" = "null" ]; then
    SUBSET_FILE_YAML="null"
else
    SUBSET_FILE_YAML="\"${SUBSET_FILE_PATH}\""
fi

cat > "${CONFIG_FILE}" << EOF
tasks_file_path: "${PREPROCESSED_TASKS_FILE}"
subset_file_path: ${SUBSET_FILE_YAML}
db_root_dir: "${DB_ROOT_DIR}"
n_processes: ${N_PROCESSES}
max_rollout_steps: ${MAX_ROLLOUT_STEPS}
max_depth: ${MAX_DEPTH}
exploration_constant: ${EXPLORATION_CONSTANT}
save_root_dir: "${SCRIPT_DIR}/results/${MODEL_NAME//\//_}/${DATASET_NAME}"
mcts_model_kwargs:
    model: "${MODEL_NAME}"
    n: 3
    top_p: 0.8
    max_tokens: 4096
    temperature: 0.8
    n_strategy: "single"
    base_url: "${OPENAI_API_BASE}"
    api_key: "${OPENAI_API_KEY}"
reward_model_kwargs: null
random_seed: 42
EOF

echo "配置文件已创建: ${CONFIG_FILE}"
echo ""

# ========== 运行 Alpha-SQL ==========
echo "========== 运行 Alpha-SQL =========="
echo "开始时间: $(date)"
start_time=$(date +%s)

cd "${SCRIPT_DIR}"  # 切换到 Alpha-SQL-2.2.4 目录
python -m alphasql.runner.mcts_runner "${CONFIG_FILE}"

end_time=$(date +%s)
echo ""
echo "完成时间: $(date)"
echo "总耗时: $((end_time - start_time)) 秒"

echo ""
echo "========== 完成 =========="
echo "结果保存在: ${SCRIPT_DIR}/results/${MODEL_NAME//\//_}/${DATASET_NAME}"

