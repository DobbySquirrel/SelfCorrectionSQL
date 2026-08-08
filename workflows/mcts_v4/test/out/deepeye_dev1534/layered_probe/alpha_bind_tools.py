"""Alpha-SQL as a column/schema bind TOOL for CTE generation (exp branch).

Wraps Alpha-SQL's schema_selection prompt (+ optional IdentifyColumnValues style hint)
WITHOUT running Alpha MCTS. Intended to fix freechain/DeepEye column-binding misses
(e.g. qid=28 FundingType vs Charter Funding Type).

Does NOT touch freechain smoke v1 scripts / running jobs.
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ALPHA_ROOT = Path("/hpc2hdd/home/sshen190/wtao565/SelfCorrectionSQL/Alpha-SQL-2.2.4")
if str(ALPHA_ROOT) not in sys.path:
    sys.path.insert(0, str(ALPHA_ROOT))

from alphasql.llm_call.prompt_factory import get_prompt  # noqa: E402


@dataclass
class BindingPack:
    selected_columns: Dict[str, List[str]] = field(default_factory=dict)
    binding_hint: str = ""
    raw_response: str = ""
    ok: bool = False
    error: str = ""


def _extract_json_obj(text: str) -> Optional[dict]:
    raw = (text or "").strip()
    m = re.search(r"```json\s*([\s\S]*?)```", raw, flags=re.I)
    if m:
        raw = m.group(1).strip()
    else:
        m = re.search(r"\{[\s\S]*\}", raw)
        if not m:
            return None
        raw = m.group(0)
    try:
        obj = json.loads(raw)
        return obj if isinstance(obj, dict) else None
    except Exception:
        return None


def _normalize_selection(obj: dict) -> Dict[str, List[str]]:
    out: Dict[str, List[str]] = {}
    for k, v in (obj or {}).items():
        if str(k).lower() in ("thinking", "reasoning", "reason"):
            continue
        if isinstance(v, list):
            cols = [str(c).strip() for c in v if str(c).strip()]
        elif isinstance(v, str) and v.strip():
            cols = [v.strip()]
        else:
            continue
        if cols:
            out[str(k)] = cols
    return out


def format_binding_hint(selected: Dict[str, List[str]], *, extra: str = "") -> str:
    lines = [
        "# Alpha-SQL schema bind (use ONLY these tables/columns unless PK/FK require more)",
    ]
    if not selected:
        lines.append("- (empty selection — fall back to full schema carefully)")
    else:
        for t, cols in selected.items():
            lines.append(f"- {t}: {', '.join(cols)}")
    if extra.strip():
        lines.append(extra.strip())
    return "\n".join(lines)


def alpha_schema_bind_tool(
    *,
    question: str,
    evidence: str,
    schema_context: str,
    llm_complete,  # callable(prompt: str, temperature: float=0.1) -> str
    temperature: float = 0.1,
    previous_thoughts: str = "",
) -> BindingPack:
    """Tool T2: Alpha schema_selection → {table: [cols]} + binding_hint for CTE gen.

    llm_complete: inject project LLM (e.g. wrap create_chat_completion) so this
    module stays free of vLLM preset wiring.
    """
    hint = (evidence or "").strip() or "None"
    if previous_thoughts.strip():
        hint = f"{hint}\n\nHere are my previous thoughts:\n{previous_thoughts.strip()}"
    try:
        prompt = get_prompt(
            template_name="schema_selection",
            template_args={
                "QUESTION": question,
                "HINT": hint,
                "SCHEMA_CONTEXT": schema_context[:14000],
            },
        )
    except Exception as e:
        return BindingPack(ok=False, error=f"prompt_factory:{e}")

    try:
        raw = llm_complete(prompt, temperature=temperature) or ""
    except Exception as e:
        return BindingPack(ok=False, error=f"llm:{e}")

    obj = _extract_json_obj(raw)
    if not obj:
        return BindingPack(ok=False, error="parse_json_failed", raw_response=raw[:2000])

    selected = _normalize_selection(obj)
    if not selected:
        return BindingPack(
            ok=False,
            error="empty_selection",
            raw_response=raw[:2000],
            selected_columns={},
        )

    return BindingPack(
        ok=True,
        selected_columns=selected,
        binding_hint=format_binding_hint(selected),
        raw_response=raw[:2000],
    )


def alpha_identify_values_tool(
    *,
    question: str,
    evidence: str,
    schema_context: str,
    llm_complete,
    temperature: float = 0.1,
) -> str:
    """Optional light tool: IdentifyColumnValues-style free text for HINT."""
    try:
        prompt = get_prompt(
            template_name="identify_column_values",
            template_args={
                "QUESTION": question,
                "HINT": evidence or "None",
                "SCHEMA_CONTEXT": schema_context[:12000],
            },
        )
    except Exception:
        return ""
    try:
        text = (llm_complete(prompt, temperature=temperature) or "").strip()
    except Exception:
        return ""
    if not text:
        return ""
    return f"Identify column values: {text[:1200]}"


def bind_for_cte(
    *,
    question: str,
    evidence: str,
    schema_context: str,
    llm_complete,
    with_value_id: bool = False,
) -> BindingPack:
    """One-shot bind pack for a CTE generation step."""
    pack = alpha_schema_bind_tool(
        question=question,
        evidence=evidence,
        schema_context=schema_context,
        llm_complete=llm_complete,
    )
    if not pack.ok:
        return pack
    extra = ""
    if with_value_id:
        extra = alpha_identify_values_tool(
            question=question,
            evidence=evidence,
            schema_context=schema_context,
            llm_complete=llm_complete,
        )
    if extra:
        pack.binding_hint = format_binding_hint(pack.selected_columns, extra=extra)
    return pack
