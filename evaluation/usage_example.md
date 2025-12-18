# 使用说明

## 修改后的功能

### 1. baseline_sql_generator.py 增强功能

现在 `baseline_sql_generator.py` 支持直接输出 TXTjson 格式，无需额外的转换步骤。

#### 新增参数：
- `--json_out`: 输出 JSON 文件路径（TXTjson格式，可选）
- `--sub_sampled_json`: sub_sampled_bird_dev_set.json文件路径（用于生成JSON输出）

#### 使用示例：

```bash
# 只输出TXT格式（原有功能）
python test/test_baseline/baseline_sql_generator.py \
    --ppl_file data/sub_sampled_bird_dev_set.json \
    --sql_out test/test_baseline/out/random_sql_10_22.txt

# 同时输出TXT和JSON格式（新功能）
python test/test_baseline/baseline_sql_generator.py \
    --ppl_file data/sub_sampled_bird_dev_set.json \
    --sql_out test/test_baseline/out/random_sql_10_22.txt \
    --json_out evaluation/predict_answer/baseline/extracted_sql_results_10_2.json \
    --sub_sampled_json data/sub_sampled_bird_dev_set.json
```

### 2. TXTjson.py 独立转换工具

`TXTjson.py` 现在可以作为独立的转换工具使用，支持命令行参数。

#### 使用示例：

```bash
# 将baseline_sql_generator.py的输出转换为TXTjson格式
python evaluation/TXTjson.py \
    --input_txt test/test_baseline/out/random_sql_10_22.txt \
    --sub_sampled_json data/sub_sampled_bird_dev_set.json \
    --output_json evaluation/predict_answer/baseline/extracted_sql_results_10_2.json
```

## 输出格式

两种方式都会生成相同格式的JSON文件：

```json
{
    "9": "SELECT COUNT(T2.`School Code`) FROM satscores AS T1 INNER JOIN frpm AS T2 ON T1.cds = T2.CDSCode WHERE T1.AvgScrMath > 560 AND T2.`Charter Funding Type` = 'Directly funded'\t----- bird -----\tcalifornia_schools",
    "10": "SELECT ...\t----- bird -----\tcalifornia_schools",
    ...
}
```

## 优势

1. **一步到位**: `baseline_sql_generator.py` 现在可以直接输出最终需要的JSON格式
2. **向后兼容**: 原有的TXT输出功能保持不变
3. **灵活性**: 可以选择只输出TXT，或者同时输出TXT和JSON
4. **独立工具**: `TXTjson.py` 仍然可以作为独立的转换工具使用
