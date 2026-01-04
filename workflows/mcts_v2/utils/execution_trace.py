# utils/execution_trace.py
"""
Execution Trace 模块：记录每一步的 SQL delta 和执行结果
"""
from typing import List, Dict, Any, Optional
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class StepRecord:
    """每一步的执行记录"""
    step_id: int
    strategy_id: str  # "S1"|"S2"|"S3"|"S4"
    action: str  # "BUILD"|"REFINE"|"FINISH"|"PROBE"
    sql_delta: str  # 这一步新增/修改的 SQL 片段
    observation: Dict[str, Any]  # 执行结果
    timestamp: Optional[float] = None  # 可选时间戳


def render_execution_trace(
    steps: List[StepRecord],
    k: int = 3,
    max_sql_lines: int = 40,
    max_sample_rows: int = 3
) -> str:
    """
    渲染 execution trace 为文本格式（用于 prompt）
    
    Args:
        steps: 步骤记录列表（按时间顺序，最旧的在前面）
        k: 只保留最近 k 步
        max_sql_lines: SQL delta 最大行数（超过则截断）
        max_sample_rows: sample 最大行数
    
    Returns:
        格式化的文本块
    """
    if not steps:
        return "# EXECUTION TRACE (Step-Aligned, Most Recent Last)\n\n(No steps yet)"
    
    # 只保留最近 k 步
    recent_steps = steps[-k:] if len(steps) > k else steps
    
    lines = ["# EXECUTION TRACE (Step-Aligned, Most Recent Last)", ""]
    
    for step in recent_steps:
        # Step 头部
        lines.append(f"Step {step.step_id} | strategy={step.strategy_id} | action={step.action}")
        
        # SQL Delta
        sql_lines = step.sql_delta.strip().split('\n')
        if len(sql_lines) > max_sql_lines:
            sql_lines = sql_lines[:max_sql_lines]
            sql_lines.append("... (truncated)")
        
        if step.sql_delta.strip():
            lines.append("SQL Delta:")
            lines.append("```sql")
            lines.extend(sql_lines)
            lines.append("```")
        else:
            # PROBE 动作可能没有 SQL delta
            if step.action == "PROBE":
                lines.append("SQL Delta: (PROBE - no SQL delta)")
            else:
                lines.append("SQL Delta: (empty)")
        
        # Execution Result
        obs = step.observation
        lines.append("Execution Result:")
        lines.append(f"- Status: {obs.get('status', 'unknown')}")
        
        if obs.get('error'):
            lines.append(f"- Error: {obs.get('error')}")
        
        if obs.get('rows') is not None:
            lines.append(f"- Rows: {obs.get('rows')}")
        
        if obs.get('columns'):
            cols = obs['columns']
            if isinstance(cols, list):
                lines.append(f"- Columns: {cols}")
            else:
                lines.append(f"- Columns: {str(cols)}")
        
        if obs.get('sample'):
            sample = obs['sample']
            # 限制 sample 行数
            if isinstance(sample, list):
                sample_display = sample[:max_sample_rows]
                lines.append(f"- Sample: {sample_display}")
                if len(sample) > max_sample_rows:
                    lines.append(f"  (showing {max_sample_rows} of {len(sample)} rows)")
            else:
                lines.append(f"- Sample: {sample}")
        
        if obs.get('runtime_ms') is not None:
            lines.append(f"- Runtime: {obs['runtime_ms']}ms")
        
        lines.append("")  # 空行分隔
    
    return "\n".join(lines)


def create_step_record(
    step_id: int,
    strategy_id: str,
    action: str,
    sql_delta: str,
    observation: Dict[str, Any],
    timestamp: Optional[float] = None
) -> StepRecord:
    """创建步骤记录的便捷函数"""
    if timestamp is None:
        timestamp = datetime.now().timestamp()
    
    return StepRecord(
        step_id=step_id,
        strategy_id=strategy_id,
        action=action,
        sql_delta=sql_delta,
        observation=observation,
        timestamp=timestamp
    )

