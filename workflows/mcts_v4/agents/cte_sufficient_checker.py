"""
子问题 CTE 充分性校验（M_verify，mcts_v4）

判断当前 CTE/SQL 及其执行结果是否足以解决当前子问题。
输入：原始问题 Q、当前子问题 q_i、前缀执行历史 H_prefix、当前 sql_i、执行结果 r_i。
输出：valid (bool)、reason (str)。
"""

import re
import json
import threading
from typing import Dict, List, Any, Optional, Tuple

import autogen


def _format_h_prefix(h_prefix: List[Dict[str, Any]]) -> str:
    if not h_prefix:
        return "(none; this is the first sub-question)"
    lines = []
    for i, h in enumerate(h_prefix, 1):
        q = h.get("q", "")
        summary = h.get("result_summary", "") or h.get("result", "")
        lines.append(f"  {i}. Sub-question: {q}\n     Result summary: {summary}")
    return "\n".join(lines)


class CTESufficientChecker:
    """M_verify：判断当前 CTE 是否足以解决本子问题"""

    def __init__(self, llm_config: Dict):
        self.llm_config = llm_config
        self._agent_lock = threading.Lock()
        self._setup_agent()

    def _setup_agent(self, temperature: float = 0.2):
        with self._agent_lock:
            llm_config = self.llm_config.copy()
            if "config_list" in llm_config:
                for c in llm_config["config_list"]:
                    c["temperature"] = temperature
                    if isinstance(c.get("openai"), dict):
                        c["openai"]["temperature"] = temperature
            self._agent = autogen.AssistantAgent(
                name="CTESufficientChecker",
                llm_config=llm_config,
                system_message=self._system_message(),
            )
            self._user_proxy = autogen.UserProxyAgent(
                name="CheckerProxy",
                human_input_mode="NEVER",
                max_consecutive_auto_reply=0,
                code_execution_config=False,
            )

    def _system_message(self) -> str:
        return """You are an expert at verifying SQL and natural language question answering. Given: the original question, the current sub-question to solve, the previously solved sub-questions and their results, and the current CTE/SQL with its execution result. Judge whether the current CTE's execution result is **directly** sufficient to answer the current sub-question (one CTE = one sub-query answer). The result must be the final answer to this sub-question, not an intermediate step (e.g. for "count of X" the result must be a single count, not a table that would need further aggregation). Output only a single JSON object: {"valid": true or false, "reason": "brief reason"}."""

    def verify(
        self,
        original_question: str,
        current_sub_question: str,
        h_prefix: List[Dict[str, Any]],
        current_cte_or_sql: str,
        execution_result_summary: str,
        execution_valid: bool = True,
        timeout_s: float = 45.0,
    ) -> Tuple[bool, str]:
        if not execution_valid:
            return False, "Execution failed"
        prefix_str = _format_h_prefix(h_prefix)
        user_msg = f"""**Original question**: {original_question}\n**Current sub-question**: {current_sub_question}\n**Already solved sub-questions and results**:\n{prefix_str}\n**Current CTE/SQL**:\n{current_cte_or_sql[:2000]}\n**Execution result summary**: {execution_result_summary}\nOutput only JSON: {{"valid": true or false, "reason": "brief reason"}}"""
        try:
            with self._agent_lock:
                chat_result = self._user_proxy.initiate_chat(
                    self._agent, message=user_msg, max_turns=1, silent=True
                )
        except Exception as e:
            print(f"[CTESufficientChecker] LLM error: {e}")
            return False, str(e)
        text = getattr(chat_result.chat_history[-1], "content", "") if hasattr(chat_result, "chat_history") and chat_result.chat_history else str(chat_result)
        valid, reason = _parse_verify_json(text)
        return valid, reason


def _parse_verify_json(text: str) -> Tuple[bool, str]:
    """Parse verify result. On no response or parse failure, default to (True, ...) to avoid blocking."""
    if not text:
        return True, "No response; default pass"
    text = text.strip()
    start = text.find("{")
    if start == -1:
        return True, "No JSON found; default pass"
    depth = 0
    end = -1
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    if end == -1:
        return True, "Incomplete JSON; default pass"
    try:
        obj = json.loads(text[start:end])
        valid = obj.get("valid", False)
        if isinstance(valid, str):
            valid = valid.lower() in ("true", "1", "yes")
        reason = obj.get("reason", "") or ""
        return bool(valid), str(reason)
    except json.JSONDecodeError:
        return True, "JSON parse failed; default pass"
