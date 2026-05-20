# Question Generation (Axis Aggregation + NL Rendering)

在 Atomic Pool 与 EIG selector 之间插入两步：**Axis Aggregation**（k-way 划分）与 **LLM Rendering**（DSL → 自然语言选项），并保持 execution-groundedness（选项仍映射到 world 分支）。

## 模块结构

| 文件 | 作用 |
|------|------|
| `data_structures.py` | `World`, `AtomicDiff`, `DecisionAxis`, `RenderedQuestion` |
| `axis_aggregation.py` | Step A：`aggregate_axes()` |
| `fidelity_validator.py` | LLM 输出结构校验 |
| `llm_rendering.py` | Step B：prompt、render、retry、DSL fallback |
| `pool_builder.py` | Step C：集成 + 转 `Question` |

## 调用方式

### 1. 在 Phase C 中开启（默认关闭）

```bash
python experiment/scripts/12_phaseC_eval.py \
  --input experiment/runs/phaseB_bird116_....jsonl \
  --gold experiment/runs/bird116_gold_hash.jsonl \
  --dataset bird116 \
  --pools atomic --selectors eig \
  --use-nl-rendering \
  --llm-preset yi_zhan_gpt-4o
```

`use_nl_rendering=False`（默认）时，`AtomicPool` 行为与改动前完全一致。

### 2. 编程接口

```python
from question_generation.pool_builder import build_pool_from_items
from experiment.pipeline.llm_client import LLMClient

questions = build_pool_from_items(
    items,
    question_text="...",
    llm_client=LLMClient(preset="yi_zhan_gpt-4o"),
    use_nl_rendering=True,
    db_path="/path/to/db.sqlite",
)
```

### 3. Case study（5 个对比 JSON）

```bash
# 需要 LLM config；无 LLM 时用 --no-llm 生成 fallback 对比
python case_studies/run_case_study.py
# 输出: case_studies/outputs/BIRD_qid_*.json
```

## 测试

```bash
cd /path/to/interactive_question
pytest question_generation/tests/ -q
```

## 已知 limitation

1. **unit_type** 使用 DSL 的 `family:parameter`（如 `aggregate:GROUP`），与论文口语中的 `group_by` 不同，但与本仓库 AST diff 一致。
2. **LLM 失败** 时退化为 DSL 标签（`fidelity_passed=False`），不会跳过该 axis。
3. **NOTA** 仅在 case study 脚本中 `append_nota=True`；主 pipeline 的 `AtomicPool` 不追加 NOTA，以免改变现有 EIG（无 open-world）行为。
4. 每个 axis 最多 **2 次** LLM 调用（`max_retries=2`）。
5. 未实现自动 readability 评估（需人工填写 case JSON 中的 `manual_eval`）。

## 未改动组件

Probing、EIG selector、user simulator、belief update、open-world instantiation 逻辑均未修改（除 `AtomicPool` / `build_pool` 可选参数与 Phase C CLI 开关）。
