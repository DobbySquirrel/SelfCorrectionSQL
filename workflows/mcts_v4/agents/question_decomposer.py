"""
问题拆分模块（mcts_v4）

将原始自然语言问题分解为子问题列表 SQ = [q1, q2, ...]，
对应 CET-SQL Algorithm 2 的 Decompose Q into SQ。
策略与 mcts_v1 的 S1/S2/S3/S7 对齐，并增加 S4 Clause-Order（按子句拆），便于论文表述。
"""

import re
import json
import os
import threading
from typing import Dict, List, Any, Optional

import autogen

# S1/S2/S3/S4/S7：S3 Evidence-Based、S4 Clause-Order 与 S1/S2/S7 一起供对比实验
DECOMPOSE_STRATEGIES = ("S1", "S2", "S3", "S4", "S7")
ENV_MIN_SUBQUESTIONS = "MCTS_DECOMPOSE_MIN_SUBQUESTIONS"


def decompose_min_subquestions() -> int:
    raw = os.environ.get(ENV_MIN_SUBQUESTIONS, "1").strip()
    try:
        return max(1, int(raw))
    except ValueError:
        return 1


def _subq_count_requirement() -> str:
    min_n = decompose_min_subquestions()
    if min_n <= 1:
        return "for very simple questions return only 1 sub-question."
    return (
        f"Return **at least {min_n} sub-questions** unless the question is a trivial "
        "single-table SELECT with no joins, filters, or aggregation. "
        f"If unsure, prefer {min_n}-3 steps over a single step."
    )


class QuestionDecomposer:
    """将原始问题分解为子问题列表，支持 S1/S2/S3/S4/S7 五种拆题策略（与 CTE 生成策略对应）。"""

    def __init__(self, llm_config: Dict, strategy: str = "S2"):
        self.llm_config = llm_config
        self.strategy = strategy if strategy in DECOMPOSE_STRATEGIES else "S2"
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
                name="QuestionDecomposer",
                llm_config=llm_config,
                system_message=self._system_message(),
            )
            self._user_proxy = autogen.UserProxyAgent(
                name="DecomposerProxy",
                human_input_mode="NEVER",
                max_consecutive_auto_reply=0,
                code_execution_config=False,
            )

    def _system_message(self) -> str:
        if self.strategy == "S1":
            return self._msg_s1()
        if self.strategy == "S3":
            return self._msg_s3()
        if self.strategy == "S4":
            return self._msg_s4()
        if self.strategy == "S7":
            return self._msg_s7()
        return self._msg_s2()

    def _msg_s1(self) -> str:
        """S1 Entity-First: single-table filter/select first, then joins (aligned with mcts_v1 S1)."""
        return (
            """You are an expert in SQL and natural language question analysis. Task: Decompose the user's **original question** into an ordered list of **sub-questions**, each corresponding to one intermediate SQL/CTE.

**S1 Entity-First decomposition** (aligned with CTE strategy S1):
- When introducing new conditions, do simple single-table filtering or column selection first, then any join.
- Sub-question order: first "single-table filter/column selection" → then "table joins" → finally "aggregation/sort/top N".
- Avoid wide multi-table joins early; keep each step simple and verifiable.

Requirements: Sub-questions must have a logical order; each step should be single and verifiable; """
            + _subq_count_requirement()
            + """ Output only a JSON array, e.g. ["sub-question 1", "sub-question 2", ...], with no other explanation."""
        )

    def _msg_s2(self) -> str:
        """S2 Relation-First: build join skeleton first, then filters, then aggregation (aligned with mcts_v1 S2)."""
        return (
            """You are an expert in SQL and natural language question analysis. Task: Decompose the user's **original question** into an ordered list of **sub-questions**, each corresponding to one intermediate SQL/CTE.

**S2 Relation-First decomposition** (aligned with CTE strategy S2):
- Prioritize correct table relationships and join paths (you may use foreign keys).
- First establish the join skeleton and relationships, then apply filters on top.
- Sub-question order: "build joins" → "filters" → "aggregation/sort/top N".

Requirements: Sub-questions must have a logical order; each step should be single and verifiable; """
            + _subq_count_requirement()
            + """ Output only a JSON array, e.g. ["sub-question 1", "sub-question 2", ...], with no other explanation."""
        )

    def _msg_s3(self) -> str:
        """S3 Evidence-Based: decompose by evidence/constraints, each step verifiable against evidence (aligned with mcts_v1 S3)."""
        return (
            """You are an expert in SQL and natural language question analysis. Task: Decompose the user's **original question** into an ordered list of **sub-questions**, each corresponding to one intermediate SQL/CTE.

**S3 Evidence-Based decomposition** (aligned with CTE strategy S3):
- If there is "additional context/evidence", sub-questions should align with verifiable points: column names, filter values, table relationships, etc.
- Each piece of evidence or constraint should map to one sub-question, or be merged into a few steps, so each step can be checked against evidence.
- Order sub-questions by evidence dependency or natural order; avoid contradicting evidence.

Requirements: Sub-questions must have a logical order; each step should be verifiable against evidence when possible; """
            + _subq_count_requirement()
            + """ Output only a JSON array, e.g. ["sub-question 1", "sub-question 2", ...], with no other explanation."""
        )

    def _msg_s4(self) -> str:
        """S4 Clause-Order: decompose by SQL clause order (data source → filter → aggregation → sort/limit)."""
        return (
            """You are an expert in SQL and natural language question analysis. Task: Decompose the user's **original question** into an ordered list of **sub-questions**, each corresponding to one intermediate SQL/CTE.

**S4 Clause-Order decomposition** (bottom-up, following SQL clause order):
- Sub-question order must match SQL writing order: first "data source / which tables and how to join" → then "WHERE conditions" → then "GROUP BY / aggregate functions" → finally "ORDER BY / LIMIT".
- Each step corresponds to one clause or one type of operation, for incremental SQL construction.
- Suited for well-structured analytical questions.

Requirements: Sub-questions must have a logical order; each step should be single and verifiable; """
            + _subq_count_requirement()
            + """ Output only a JSON array, e.g. ["sub-question 1", "sub-question 2", ...], with no other explanation."""
        )

    def _msg_s7(self) -> str:
        """S7 Grain/Key-Based: determine result grain first, then aggregate per table, then join at unified grain (aligned with mcts_v1 S7)."""
        return (
            """You are an expert in SQL and natural language question analysis. Task: Decompose the user's **original question** into an ordered list of **sub-questions**, each corresponding to one intermediate SQL/CTE.

**S7 Grain/Key-Based decomposition** (aligned with CTE strategy S7):
- First determine the result grain (key set, e.g. customer_id + year_month).
- Sub-question order: "determine grain/keys" → "aggregate each fact table at that grain" → "dimension tables keep or align grain" → "join at unified grain for final result".
- Suited for cases where multi-table joins cause blow-up or where aggregate-then-join is needed.

Requirements: Sub-questions must have a logical order; each step should be single and verifiable; """
            + _subq_count_requirement()
            + """ Output only a JSON array, e.g. ["sub-question 1", "sub-question 2", ...], with no other explanation."""
        )

    def decompose(
        self,
        question: str,
        schema_info: str,
        additional_context: str = "",
        timeout_s: float = 60.0,
    ) -> List[str]:
        """
        将原始问题分解为子问题列表。

        Args:
            question: 原始自然语言问题
            schema_info: 数据库 schema 文本
            additional_context: 额外上下文（如 evidence）
            timeout_s: 超时秒数

        Returns:
            子问题字符串列表，至少包含一个元素（原题或拆分结果）
        """
        user_msg = f"""**Original question**:
{question}

**Database schema** (excerpt):
{schema_info[:3000]}{"..." if len(schema_info) > 3000 else ""}

**Additional context** (if any):
{additional_context or "None"}

Decompose the above question into an ordered list of sub-questions. Output only a single JSON array, e.g. ["sub-question 1", "sub-question 2", ...]. Use the same language as the original question for each sub-question."""

        min_n = decompose_min_subquestions()

        def _call_decomposer(message: str) -> List[str]:
            try:
                with self._agent_lock:
                    chat_result = self._user_proxy.initiate_chat(
                        self._agent,
                        message=message,
                        max_turns=1,
                        silent=True,
                    )
            except Exception as e:
                print(f"[QuestionDecomposer] LLM call failed: {e}")
                return [question]
            text = _last_content(chat_result)
            parsed = _parse_sub_questions_json(text)
            return parsed if parsed else [question]

        sub_questions = _call_decomposer(user_msg)
        if len(sub_questions) < min_n:
            retry_msg = (
                user_msg
                + f"\n\nIMPORTANT: You must return **at least {min_n}** sub-questions "
                f"as a JSON array (currently too few steps)."
            )
            retried = _call_decomposer(retry_msg)
            if len(retried) >= len(sub_questions):
                sub_questions = retried
        if len(sub_questions) < min_n:
            print(
                f"[QuestionDecomposer] got {len(sub_questions)} sub-questions, "
                f"min={min_n}; keeping best effort"
            )
        return sub_questions


def _last_content(chat_result) -> str:
    if hasattr(chat_result, "chat_history") and chat_result.chat_history:
        last = chat_result.chat_history[-1]
        return getattr(last, "content", "") or str(last)
    return str(chat_result)


def _parse_sub_questions_json(text: str) -> List[str]:
    if not text or not isinstance(text, str):
        return []
    text = text.strip()
    # 去掉可能的 markdown 代码块
    if "```" in text:
        m = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
        if m:
            text = m.group(1).strip()
    s = text
    # 找第一个 [ 到匹配的 ]
    start = s.find("[")
    if start == -1:
        return []
    depth = 0
    end = -1
    for i in range(start, len(s)):
        if s[i] == "[":
            depth += 1
        elif s[i] == "]":
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    if end == -1:
        return []
    try:
        arr = json.loads(s[start:end])
        if isinstance(arr, list):
            return [str(x).strip() for x in arr if str(x).strip()]
    except json.JSONDecodeError:
        pass
    return []
