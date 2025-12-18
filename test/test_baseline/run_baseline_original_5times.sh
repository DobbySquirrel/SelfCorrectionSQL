#!/bin/bash
# 运行 baseline，原始顺序，5次生成（使用5个不同温度）
# 使用端口 8009 的模型服务

python test/test_baseline/baseline_sql_generator_table_permutation.py \
  --ppl_file "${1:-data/subset_ppl_dev_python.json}" \
  --experiment only_temps \
  --temperature_list "0.1,0.2,0.3,0.4,0.5" \
  --sql_out "${2:-test/test_baseline/out/baseline_original_5times.txt}" \
  --analysis_out "${3:-test/test_baseline/out/baseline_original_5times_analysis.json}" \
  --max_workers 32

