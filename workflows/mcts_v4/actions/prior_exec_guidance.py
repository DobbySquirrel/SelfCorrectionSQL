#!/usr/bin/env python3
"""Build next-step guidance from intermediate CTE execution results.

Two levels:
  1) compact summary + few sample rows (always, when inject is on)
  2) optional LLM agent that writes restricted Python to analyze `rows`
     conditioned on the *next* sub-question, then returns printed findings.
"""

from __future__ import annotations

import ast
import io
import re
import traceback
from collections import Counter
from contextlib import redirect_stdout
from typing import Any, Dict, List, Optional, Tuple

from workflows.mcts_v4.actions.deepeye_cte_plugin import _llm, format_result_preview

_ANALYZE_PROMPT = """# Task
You write short Python to analyze an intermediate SQL result table (`rows`: list of tuples).
Findings must help the NEXT CTE sub-step (join keys, grain, filters, aggregates) — not generic stats.

# Constraints
- Use ONLY the provided `rows` variable and these names already in scope:
  Counter, len, min, max, sum, sorted, set, list, dict, tuple, range, enumerate, zip,
  abs, round, isinstance, str, int, float, bool, print
- NO imports, NO file/network/OS access.
- Print at most 12 short lines of actionable guidance for the next SQL step.
- Prefer: column roles for next join/filter, cardinality, top values that look like keys/categories,
  empty/suspicious patterns, what NOT to recompute.
- Do NOT invent columns not present in the sample; index columns as c0,c1,... if unnamed.

# Next sub-step (what the following CTE must do):
{next_subq}

# Current intermediate preview (first rows):
{preview}

# n_rows (this prefix exec): {n_rows}

# Output ONLY a Python code block:
```python
# your code; print findings
```
"""


def compact_prior_exec_summary(
    rows: Optional[List[tuple]],
    err: Optional[str] = None,
    *,
    max_rows: int = 5,
) -> str:
    if err:
        return f"Prior CTE exec ERROR: {err[:200]}"
    if rows is None:
        return "Prior CTE exec: (no rows object)"
    n = len(rows)
    ncols = len(rows[0]) if rows and isinstance(rows[0], (tuple, list)) else 0
    preview = format_result_preview(rows, None, max_rows=max_rows)
    return (
        f"Prior CTE exec summary: n_rows={n} n_cols={ncols}\n"
        f"Sample rows (≤{max_rows}):\n{preview}"
    )


def _extract_code(text: str) -> str:
    raw = (text or "").strip()
    m = re.search(r"```(?:python)?\s*([\s\S]*?)```", raw, re.I)
    if m:
        return m.group(1).strip()
    return raw


def _code_is_safe(code: str) -> Tuple[bool, str]:
    """Reject imports / attribute dunders / dangerous calls via AST."""
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return False, f"syntax: {e}"
    banned_names = {
        "__import__",
        "eval",
        "exec",
        "open",
        "compile",
        "input",
        "breakpoint",
        "getattr",
        "setattr",
        "delattr",
        "globals",
        "locals",
        "vars",
        "dir",
        "type",
        "memoryview",
        "bytearray",
        "help",
    }
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            return False, "import_forbidden"
        if isinstance(node, ast.Attribute):
            if str(getattr(node, "attr", "")).startswith("__"):
                return False, "dunder_attr"
        if isinstance(node, ast.Name) and node.id in banned_names:
            return False, f"banned_name:{node.id}"
        if isinstance(node, ast.Call):
            f = node.func
            if isinstance(f, ast.Name) and f.id in banned_names:
                return False, f"banned_call:{f.id}"
    return True, "ok"


def _safe_run_python(code: str, rows: List[tuple], timeout_lines: int = 40) -> Tuple[str, Optional[str]]:
    ok, why = _code_is_safe(code)
    if not ok:
        return "", f"unsafe:{why}"
    safe_builtins = {
        "abs": abs,
        "bool": bool,
        "dict": dict,
        "enumerate": enumerate,
        "float": float,
        "int": int,
        "isinstance": isinstance,
        "len": len,
        "list": list,
        "max": max,
        "min": min,
        "print": print,
        "range": range,
        "round": round,
        "set": set,
        "sorted": sorted,
        "str": str,
        "sum": sum,
        "tuple": tuple,
        "zip": zip,
        "True": True,
        "False": False,
        "None": None,
    }
    g: Dict[str, Any] = {
        "__builtins__": safe_builtins,
        "rows": list(rows[:5000]),  # cap for safety/latency
        "Counter": Counter,
    }
    buf = io.StringIO()
    try:
        with redirect_stdout(buf):
            exec(compile(code, "<prior_exec_agent>", "exec"), g, g)  # noqa: S102
    except Exception as e:
        return buf.getvalue()[:2000], f"exec_err:{type(e).__name__}:{e}"
    out = buf.getvalue().strip()
    lines = out.splitlines()
    if len(lines) > timeout_lines:
        out = "\n".join(lines[:timeout_lines]) + "\n...(truncated)"
    return out[:3000], None


def analyze_prior_exec_with_agent(
    *,
    rows: Optional[List[tuple]],
    err: Optional[str],
    next_subq: str,
    client: Any,
    model: str,
    max_sample_rows: int = 8,
) -> Dict[str, Any]:
    """Return {guidance, audit}. Falls back to compact summary on failure."""
    base = compact_prior_exec_summary(rows, err, max_rows=5)
    audit: Dict[str, Any] = {"mode": "compact_only", "agent_ok": False}
    if err or rows is None:
        audit["reason"] = "no_rows"
        return {"guidance": base, "audit": audit}

    preview = format_result_preview(rows, None, max_rows=max_sample_rows)
    prompt = _ANALYZE_PROMPT.format(
        next_subq=(next_subq or "").strip()[:500],
        preview=preview[:2500],
        n_rows=len(rows),
    )
    try:
        text = _llm(prompt, client=client, model=model, temperature=0.2)
        code = _extract_code(text)
        printed, run_err = _safe_run_python(code, rows)
        audit.update(
            {
                "mode": "agent_python",
                "code_preview": (code or "")[:400],
                "run_err": run_err,
                "printed_len": len(printed or ""),
            }
        )
        if printed and not run_err:
            audit["agent_ok"] = True
            guidance = (
                f"{base}\n\n# Agent analysis for next sub-step:\n{printed}"
            )
            return {"guidance": guidance[:4000], "audit": audit}
        # retry compact + any partial print
        if printed:
            guidance = f"{base}\n\n# Partial agent output:\n{printed}"
            return {"guidance": guidance[:4000], "audit": audit}
    except Exception as e:
        audit["mode"] = "agent_exception"
        audit["error"] = f"{type(e).__name__}:{e}"
        audit["trace"] = traceback.format_exc()[-400:]

    return {"guidance": base, "audit": audit}


def build_prior_exec_hint_block(guidance: str) -> str:
    g = (guidance or "").strip()
    if not g:
        return ""
    return (
        "\n\n# PRIOR INTERMEDIATE EXEC (use when writing the next CTE)\n"
        "- Treat this as evidence about grain/columns/values of preceding CTE output.\n"
        "- Prefer FROM/JOIN preceding CTE names; do not ignore these signals.\n"
        f"{g}\n"
    )
