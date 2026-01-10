# ==========================================================
# Strategy Injection (for SimpleRolloutWorkflow, no MASTER prompt)
# ==========================================================
from dataclasses import dataclass
from typing import Optional, Literal, Tuple

StrategyMode = Literal[
    "FORCE_S1", "FORCE_S2", "FORCE_S3",
    "NONE",
    "LLM_PICK_ONCE",
]

@dataclass
class StrategyConfig:
    """
    mode:
      - FORCE_Sx: always inject Sx policy into prompts
      - NONE: inject nothing (baseline)
      - LLM_PICK_ONCE: at depth=0 ask model to pick strategy once, then lock for later steps
    """
    mode: StrategyMode = "FORCE_S2"
    # 如果你想在一次run中固定策略，不允许后续prompt再出现"可切换"表述，就保持True
    lock_after_picked: bool = True


GLOBAL_STRATEGY_CONFIG = StrategyConfig(mode="FORCE_S2", lock_after_picked=True)


# ---- 策略手册（给 CTE/SQL 生成器看的文本）----
_STRATEGY_DESCRIPTIONS = {
    "S1": """S1 Entity-First:
- If you introduce a new value filter (keyword/category/date/currency/segment) not confirmed, first sanity-check via safe assumptions:
  * prefer simple filters on one table before joins
  * prefer explicit WHERE on known columns, avoid guessing enums
- Avoid wide joins early.""",
    
    "S2": """S2 Relation-First:
- First ensure join path correctness (use FK hints from schema/foreign_key text).
- Build join skeleton first, then add filters.""",
    
    "S3": """S3 Evidence-Based:
- When creating a CTE, ALWAYS validate it against the evidence fields provided in the dataset.
- Use the evidence information to verify column names, filter values, join conditions, and data constraints.
- If the evidence contains specific values, column names, or relationships, incorporate them into your CTE.
- Cross-reference your CTE logic with the evidence to ensure correctness before proceeding.
- If evidence suggests a different approach, adjust your CTE accordingly."""
}

# 完整的策略手册（用于 LLM_PICK_ONCE 模式的选择阶段）
_FULL_STRATEGY_HANDBOOK = f"""
STRATEGYs:
{_STRATEGY_DESCRIPTIONS['S1']}

{_STRATEGY_DESCRIPTIONS['S2']}

{_STRATEGY_DESCRIPTIONS['S3']}
"""

def build_strategy_injection_text(
    mode: StrategyMode,
    fixed_strategy: Optional[str] = None,
    picked_strategy: Optional[str] = None,
    picked_strategy_thought: Optional[str] = None,
    depth: int = 0,
) -> str:
    """
    Return text to append into node.additional_context.
    - mode NONE => ""
    - FORCE_Sx => fixed policy
    - LLM_PICK_ONCE:
        depth=0 => ask model to output a STRATEGY line
        depth>0 => inject locked strategy policy (picked_strategy required)
    """
    if mode == "NONE":
        return ""

    # FORCE
    if mode.startswith("FORCE_"):
        s = mode.replace("FORCE_", "")
        # 只返回当前策略的说明，不包含其他策略
        strategy_desc = _STRATEGY_DESCRIPTIONS.get(s, "")
        # 对于 S3，特别强调使用 evidence 进行验证
        return f"""
[GLOBAL STRATEGY MODE: {mode}]
{strategy_desc}

**Additional Context are essential requirements**
- If "Additional context" provides filtering conditions, you MUST:
  1. Include the relevant column in your CTE
  2. Apply the filter condition (WHERE clause) in your CTE

"""

    # LLM pick once
    if mode == "LLM_PICK_ONCE":
        if depth == 0 and not picked_strategy:
            # depth=0 时需要所有策略说明，因为模型要选择
            # 注意：这个函数返回的文本会用于策略选择阶段，不是CTE生成阶段
            return f"""
**STRATEGY SELECTION REQUIRED** (Root Node)

You MUST select ONE strategy from S1, S2, or S3 based on the question and schema.
Analyze the question and choose the most appropriate strategy:
- S1 (Entity-First): Use when you need to verify entity/value constraints first
- S2 (Relation-First): Use when join paths are uncertain
- S3 (Evidence-Based): Use when evidence fields are available in the dataset and you want to validate CTEs against evidence

**CRITICAL**: You MUST output your response in JSON format with a "strategy" field containing your chosen strategy (S1, S2, or S3).

Example JSON format:
```json
{{
  "thought": "I choose S1 because...",
  "strategy": "S1"
}}
```

{_FULL_STRATEGY_HANDBOOK}
"""
        else:
            # depth>0 时只需要已选择策略的说明
            s = picked_strategy or fixed_strategy or "S2"
            strategy_desc = _STRATEGY_DESCRIPTIONS.get(s, "")
            # 对于 S3，添加额外的 evidence 验证说明
            if s == "S3":
                return f"""
{strategy_desc}

**⚠️ CRITICAL: Evidence Validation (S3 Strategy)**
- If the "Additional context" field contains evidence information from the dataset (look for text starting with "Evidence from other related questions" or similar patterns), you MUST use it to validate your CTE:
  * Check if column names in your CTE match those mentioned in the evidence
  * Verify filter values against evidence-provided values
  * Ensure join conditions align with evidence-suggested relationships
  * Cross-reference data constraints and constraints mentioned in evidence
  * If your CTE contradicts the evidence, you MUST revise it before proceeding
  * The evidence is a critical validation source - do not ignore it
- If "Additional context" does not contain evidence information, proceed with standard CTE generation while following evidence-based validation principles when evidence becomes available in later steps
"""
            return f"""
{strategy_desc}
"""

    # fallback
    return ""


def build_strategy_selection_prompt(question: str, schema_info: str, additional_context: str = "") -> str:
    """
    构建策略选择的prompt（单独调用，JSON格式输出）
    
    Args:
        question: 自然语言问题
        schema_info: 数据库schema信息
        additional_context: 额外上下文
        
    Returns:
        策略选择的prompt文本
    """
    return f"""# STRATEGY SELECTION TASK

You need to select ONE strategy from S1, S2, or S3 for solving the following SQL generation task.

Note: S3 (Evidence-Based) is recommended when evidence fields are available in the dataset, as it uses evidence to validate CTEs during generation.

**Question**: {question}

**Database Schema**:
{schema_info}

{f"**Additional Context**: {additional_context}" if additional_context else ""}

**Available Strategies**:

{_FULL_STRATEGY_HANDBOOK}

**CRITICAL**: You MUST output your response in JSON format with a "strategy" field containing your chosen strategy (S1, S2, or S3).

Example JSON format:
```json
{{
  "thought": "I choose S1 because the question requires filtering by specific values that need verification first.",
  "strategy": "S1"
}}
```

**Output Requirements**:
- Output MUST be valid JSON wrapped in ```json code block
- The "strategy" field MUST be exactly one of: "S1", "S2", or "S3"
- Include a "thought" field explaining your choice
"""


def extract_strategy_from_json(response: str) -> Tuple[Optional[str], Optional[str]]:
    """
    从JSON响应中提取策略和thought
    
    Args:
        response: LLM的JSON响应
        
    Returns:
        (策略字符串 (S1/S2/S3), thought字符串) 或 (None, None)
    """
    import json
    import re
    
    try:
        # 1. 优先提取 ```json 代码块中的内容
        json_str = None
        json_block_match = re.search(r'```json\s*(.*?)\s*```', response, re.DOTALL)
        if json_block_match:
            json_str = json_block_match.group(1).strip()
        else:
            # 尝试提取普通代码块
            code_block_match = re.search(r'```\s*(.*?)\s*```', response, re.DOTALL)
            if code_block_match:
                potential_json = code_block_match.group(1).strip()
                if potential_json.strip().startswith('{'):
                    json_str = potential_json
        
        # 2. 如果代码块提取失败，尝试从文本中提取JSON对象
        if not json_str:
            brace_count = 0
            start_idx = -1
            for i in range(len(response) - 1, -1, -1):
                if response[i] == '}':
                    if brace_count == 0:
                        start_idx = i
                    brace_count += 1
                elif response[i] == '{':
                    brace_count -= 1
                    if brace_count == 0 and start_idx != -1:
                        json_str = response[i:start_idx + 1]
                        break
        
        if not json_str:
            return None
        
        # 3. 解析JSON
        json_str = json_str.strip()
        data = json.loads(json_str)
        
        # 4. 提取策略字段和thought
        strategy_str = data.get("strategy", "").upper().strip()
        thought_str = data.get("thought", "").strip()
        
        # 5. 验证策略
        valid_strategies = ["S1", "S2", "S3"]
        if strategy_str in valid_strategies:
            return strategy_str, thought_str if thought_str else None
        
        return None, None
        
    except json.JSONDecodeError as e:
        print(f"[策略提取] JSON解析失败: {e}")
        return None, None
    except Exception as e:
        print(f"[策略提取] 提取策略时出错: {e}")
        return None, None

