# ==========================================================
# Strategy Injection (for SimpleRolloutWorkflow, no MASTER prompt)
# ==========================================================
from dataclasses import dataclass
from typing import Optional, Literal

StrategyMode = Literal[
    "FORCE_S1", "FORCE_S2", "FORCE_S3", "FORCE_S4",
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
    mode: StrategyMode = "FORCE_S4"
    # 如果你想在一次run中固定策略，不允许后续prompt再出现"可切换"表述，就保持True
    lock_after_picked: bool = True


GLOBAL_STRATEGY_CONFIG = StrategyConfig(mode="FORCE_S4", lock_after_picked=True)


# ---- 策略手册（给 CTE/SQL 生成器看的文本）----
_SHARED_CONSTRAINTS = """
Shared constraints:
- Generate ONE step only (one CTE OR <END> OR one final SELECT depending on generator).
- Never invent tables/columns. Use schema only.
- Prefer executable, minimal joins.
"""

_STRATEGY_DESCRIPTIONS = {
    "S1": """S1 Entity-First:
- If you introduce a new value filter (keyword/category/date/currency/segment) not confirmed, first sanity-check via safe assumptions:
  * prefer simple filters on one table before joins
  * prefer explicit WHERE on known columns, avoid guessing enums
- Avoid wide joins early.""",
    
    "S2": """S2 Relation-First:
- First ensure join path correctness (use FK hints from schema/foreign_key text).
- Build join skeleton first, then add filters.""",
    
    "S3": """S3 Proactive:
- Upfront disambiguation: if schema is large/ambiguous, create a robust intermediate CTE with only necessary columns.
- Avoid fancy expressions until you have the correct grain.""",
    
    "S4": """S4 Reactive:
- Try a plausible CTE quickly; keep it simple.
- Prefer small incremental CTEs; rely on execution feedback (even though you don't have REFINE loop here)."""
}

# 完整的策略手册（用于 LLM_PICK_ONCE 模式的选择阶段）
_FULL_STRATEGY_HANDBOOK = f"""[STRATEGY HANDBOOK]

{_SHARED_CONSTRAINTS}

{_STRATEGY_DESCRIPTIONS['S1']}

{_STRATEGY_DESCRIPTIONS['S2']}

{_STRATEGY_DESCRIPTIONS['S3']}

{_STRATEGY_DESCRIPTIONS['S4']}
"""

def build_strategy_injection_text(
    mode: StrategyMode,
    fixed_strategy: Optional[str] = None,
    picked_strategy: Optional[str] = None,
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
        return f"""
[GLOBAL STRATEGY MODE: {mode}]
You MUST follow strategy {s} for this rollout. Do NOT switch.

{_SHARED_CONSTRAINTS}

{strategy_desc}

[ACTIVE STRATEGY = {s}]
"""

    # LLM pick once
    if mode == "LLM_PICK_ONCE":
        if depth == 0 and not picked_strategy:
            # depth=0 时需要所有策略说明，因为模型要选择
            return f"""
[GLOBAL STRATEGY MODE: LLM_PICK_ONCE]
First, choose ONE strategy from S1/S2/S3/S4 for THIS rollout.
Then generate the next CTE.

Output format requirement (very important):
- First line MUST be exactly: -- STRATEGY: S1|S2|S3|S4
- Second line onward: the CTE text OR <END>

{_FULL_STRATEGY_HANDBOOK}
"""
        else:
            # depth>0 时只需要已选择策略的说明
            s = picked_strategy or fixed_strategy or "S4"
            strategy_desc = _STRATEGY_DESCRIPTIONS.get(s, "")
            return f"""
[GLOBAL STRATEGY MODE: LLM_PICK_ONCE]
You have already chosen strategy {s} for this rollout. You MUST follow it. Do NOT switch.

{_SHARED_CONSTRAINTS}

{strategy_desc}

[ACTIVE STRATEGY = {s}]
"""

    # fallback
    return ""

