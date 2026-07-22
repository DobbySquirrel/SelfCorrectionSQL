#!/usr/bin/env python3
"""Trim DeepEye CTE candidates that over-solve the full question into a step-scoped CTE.

Keeps only what the *current* sub-step needs; drops premature ORDER BY/LIMIT,
final-answer projections, and filters belonging to later steps.
"""

from __future__ import annotations

import re
from typing import Any, Dict

from workflows.mcts_v4.actions.deepeye_cte_plugin import _llm

_TRIM_PROMPT = """# Task
You MUST rewrite the candidate into ONE intermediate CTE that implements EXACTLY the Current sub-step below — nothing else.

# Hard constraints (mandatory)
1. The CTE result must be a valid implementation of ONLY this sub-step text:
   {sub_question}
2. Do NOT add filters / joins / aggregates / ORDER BY / LIMIT / columns that the Current sub-step does not ask for.
3. You are NOT given the full end-user question. Do NOT invent a final answer or later-step logic.
4. If the candidate contains extra logic beyond the Current sub-step
   (e.g. Phone-only projection, district filter, ranking LIMIT 1) while the Current sub-step is only a join
   or an earlier-scope filter — DELETE that extra logic.
5. Prefer a wider intermediate table needed for THIS sub-step (keys + needed attributes), not a scalar final answer.
6. Output ONE CTE only: WITH step_trim AS ( ... ) SELECT * FROM step_trim;

# Current sub-step (the ONLY thing you may implement):
{sub_question}

# Preceding CTEs (reuse via FROM/JOIN when helpful):
{preceding}

# Candidate CTE/SQL to rewrite (may be over-complete — strip it down):
```sql
{cte}
```

# Output:
Return ONLY the rewritten SQL in a ```sql``` block. No explanation.
"""


def _extract_sql(text: str) -> str:
    raw = (text or "").strip()
    m = re.search(r"```sql\s*([\s\S]*?)```", raw, re.I)
    if m:
        return m.group(1).strip().rstrip(";")
    m = re.search(r"```\s*([\s\S]*?)```", raw)
    if m:
        return m.group(1).strip().rstrip(";")
    m = re.search(r"((?:WITH\b[\s\S]+)|(?:SELECT\b[\s\S]+))", raw, re.I)
    return (m.group(1).strip().rstrip(";") if m else raw)


def trim_cte_to_substep(
    *,
    cte: str,
    sub_question: str,
    question: str,
    preceding_ctes: list,
    client: Any,
    model: str,
    sql_to_cte_fn,
    temperature: float = 0.1,
) -> Dict[str, Any]:
    """Return {cte, ok, changed, audit}."""
    src = (cte or "").strip()
    if not src:
        return {"cte": "", "ok": False, "changed": False, "audit": {"reason": "empty"}}
    preceding = "\n\n".join(preceding_ctes) if preceding_ctes else "(none)"
    prompt = _TRIM_PROMPT.format(
        sub_question=(sub_question or "").strip()[:800],
        preceding=preceding[:4000],
        cte=src[:3500],
    )
    audit: Dict[str, Any] = {"mode": "trim_cte_no_full_q"}
    try:
        text = _llm(prompt, client=client, model=model, temperature=temperature)
        fixed = _extract_sql(text)
        new_cte = sql_to_cte_fn(fixed, step_name="trim_step", preceding_ctes=preceding_ctes)
        if not new_cte:
            audit["reason"] = "parse_fail"
            return {"cte": src, "ok": False, "changed": False, "audit": audit}
        norm = lambda s: re.sub(r"\s+", " ", (s or "").lower()).strip()
        changed = norm(new_cte) != norm(src)
        audit["changed"] = changed
        audit["ok"] = True
        return {"cte": new_cte, "ok": True, "changed": changed, "audit": audit}
    except Exception as e:
        audit["reason"] = f"{type(e).__name__}:{e}"
        return {"cte": src, "ok": False, "changed": False, "audit": audit}
