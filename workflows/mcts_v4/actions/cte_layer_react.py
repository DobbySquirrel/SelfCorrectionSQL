#!/usr/bin/env python3
"""Step-0 ReAct to discriminate CTE clusters (framework hook).

Only chooses among existing clusters (KEEP winner or SWITCH). No free-form SQL rewrite.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from openai import OpenAI

from workflows.mcts_v4.actions.deepeye_cte_plugin import exec_rows, format_result_preview
from workflows.mcts_v4.query_clarifier.llm_client import extract_json
from workflows.mcts_v4.utils.llm_chat import create_chat_completion

TMPL_A = """You are selecting the best intermediate CTE for THIS plan step by probing SQLite.

Original question: {question}
Evidence: {evidence}
Current sub-question (this CTE step): {sub_question}
Schema:
{schema}

Baseline winner CTE (from consistency/BR):
{baseline_cte}

Other cluster CTEs (C0 is baseline; Ci are alternatives):
{clusters_block}

=== Probe ===
Prefer ONE SQL that contrasts two interpretations relevant to this sub-question
(two filters / joins / aggregations / output columns). Use Schema tables only.

=== Decision gate ===
Default KEEP (stay with C0).
SWITCH to Ci only if History clearly shows Ci better matches the sub-question
(e.g. required column present, null fixed, absurd metric avoided).
Do NOT switch for style-only differences.

Budget left: {steps_left}
History:
{history}

JSON only:
{{"a":"sql","t":"...","q":"SELECT ..."}}
or
{{"a":"done","fix":"KEEP|SWITCH","to":"C0|C1|C2","why":"cite History"}}
"""


def _llm_json(client: OpenAI, model: str, prompt: str) -> Dict[str, Any]:
    resp = create_chat_completion(
        client,
        model=model,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.2,
    )
    text = resp.choices[0].message.content or ""
    return {"raw": text[:2000], "json": extract_json(text) or {}}


def _fmt_hist(hist: List[dict]) -> str:
    if not hist:
        return "(empty)"
    lines = []
    for h in hist:
        if h.get("type") == "sql":
            lines.append(
                f"t: {h.get('t')}\nq: {h.get('q')}\n"
                f"obs: ok={h['obs'].get('ok')} preview={json.dumps(h['obs'].get('preview'), ensure_ascii=False)[:260]} "
                f"err={h['obs'].get('error','')}"
            )
        else:
            lines.append(f"DONE fix={h.get('fix')} to={h.get('to')} why={h.get('why')}")
    return "\n--\n".join(lines)


def _has_contrast(hist: List[dict]) -> bool:
    previews = []
    for h in hist:
        if h.get("type") != "sql" or not h.get("obs", {}).get("ok"):
            continue
        pv = h["obs"].get("preview") or []
        if not pv:
            continue
        previews.append(json.dumps(pv, sort_keys=True, default=str))
        row0 = pv[0]
        if isinstance(row0, dict) and len(row0) >= 2 and len(set(map(str, row0.values()))) >= 2:
            return True
    return len(set(previews)) >= 2


def _run_sql(db_path: Path, sql: str, limit: int = 5) -> dict:
    rows, err = exec_rows(db_path, sql)
    if err is not None:
        return {"ok": False, "preview": [], "error": str(err)[:200]}
    preview = [{"c" + str(i): v for i, v in enumerate(r)} for r in (rows or [])[:limit]]
    return {
        "ok": True,
        "preview": preview,
        "text": format_result_preview(rows, None, max_rows=limit)[:300],
        "error": "",
    }


def react_select_cte_cluster(
    *,
    db_path: Path,
    schema: str,
    question: str,
    evidence: str,
    sub_question: str,
    clusters: List[Dict[str, Any]],
    winner_cte: str,
    client: OpenAI,
    model: str,
    budget: int = 4,
    max_clusters: int = 3,
) -> Dict[str, Any]:
    """Return {winner_cte, switched, audit}."""
    audit: Dict[str, Any] = {
        "enabled": True,
        "skipped": False,
        "budget": budget,
        "traces": [],
        "llm_calls": 0,
    }
    if not winner_cte or not clusters:
        audit["skipped"] = True
        audit["reason"] = "no_winner_or_clusters"
        return {"winner_cte": winner_cte, "switched": False, "audit": audit}

    # order: winner first as C0, then other largest clusters
    ordered: List[Dict[str, Any]] = []
    win_norm = re.sub(r"\s+", " ", (winner_cte or "").strip().lower())
    for c in clusters:
        cte = (c.get("cte") or "").strip()
        if not cte:
            continue
        if re.sub(r"\s+", " ", cte.lower()) == win_norm:
            ordered.insert(0, c)
        else:
            ordered.append(c)
    # dedupe
    seen = set()
    uniq = []
    for c in ordered:
        key = re.sub(r"\s+", " ", (c.get("cte") or "").lower())
        if key in seen:
            continue
        seen.add(key)
        uniq.append(c)
    uniq = uniq[:max_clusters]
    if len(uniq) < 2:
        audit["skipped"] = True
        audit["reason"] = "single_cluster"
        return {"winner_cte": winner_cte, "switched": False, "audit": audit}

    labels = []
    block_lines = []
    for i, c in enumerate(uniq):
        lab = f"C{i}"
        labels.append(lab)
        block_lines.append(
            f"{lab} (size={c.get('size')}, cons={c.get('consistency')}):\n{(c.get('cte') or '')[:500]}"
        )
    clusters_block = "\n\n".join(block_lines)
    baseline_cte = uniq[0].get("cte") or winner_cte

    hist: List[dict] = []
    steps_left = budget
    decision: Optional[dict] = None
    finish_rejects = 0

    for _ in range(budget + 2):
        prompt = TMPL_A.format(
            question=question,
            evidence=evidence or "None",
            sub_question=sub_question,
            schema=(schema or "")[:5000],
            baseline_cte=baseline_cte[:1200],
            clusters_block=clusters_block[:3500],
            steps_left=steps_left,
            history=_fmt_hist(hist),
        )
        try:
            out = _llm_json(client, model, prompt)
            audit["llm_calls"] += 1
        except Exception as e:
            audit["traces"].append({"error": str(e)})
            break
        data = out.get("json") or {}
        audit["traces"].append({"steps_left": steps_left, "parsed": data, "raw_head": (out.get("raw") or "")[:240]})
        a = str(data.get("a") or "").strip().lower()

        if a in ("done", "finish"):
            n_ok = sum(1 for h in hist if h.get("type") == "sql" and h.get("obs", {}).get("ok"))
            if n_ok < 1 or not _has_contrast(hist):
                finish_rejects += 1
                hist.append(
                    {
                        "type": "sql",
                        "t": "system",
                        "q": "--",
                        "obs": {"ok": False, "preview": [], "error": "FINISH rejected: need contrast"},
                    }
                )
                if finish_rejects >= 2 or steps_left <= 0:
                    decision = {"fix": "KEEP", "to": "C0", "why": "finish rejected"}
                    break
                continue
            fix = str(data.get("fix") or "KEEP").strip().upper()
            to = str(data.get("to") or "C0").strip().upper()
            if fix not in ("KEEP", "SWITCH"):
                fix = "KEEP"
            if to not in labels:
                to = "C0"
            if fix == "KEEP":
                to = "C0"
            decision = {"fix": fix, "to": to, "why": str(data.get("why") or "")[:400]}
            hist.append({"type": "done", **decision})
            break

        sql = str(data.get("q") or data.get("sql") or "").strip()
        if not sql or steps_left <= 0 or sql.startswith("--"):
            continue
        obs = _run_sql(db_path, sql)
        steps_left -= 1
        hist.append({"type": "sql", "t": str(data.get("t") or "")[:200], "q": sql, "obs": obs})

        if steps_left == 0 and decision is None:
            decision = {"fix": "KEEP", "to": "C0", "why": "budget end -> KEEP"}
            hist.append({"type": "done", **decision})
            break

    if decision is None:
        decision = {"fix": "KEEP", "to": "C0", "why": "fallback KEEP"}

    to = decision.get("to") or "C0"
    try:
        idx = labels.index(to)
    except ValueError:
        idx = 0
    chosen = (uniq[idx].get("cte") or winner_cte).strip()
    switched = decision.get("fix") == "SWITCH" and idx != 0
    if not switched:
        chosen = winner_cte
        decision["fix"] = "KEEP"
        decision["to"] = "C0"

    audit["decision"] = decision
    audit["history"] = hist
    audit["switched"] = switched
    audit["n_clusters_considered"] = len(uniq)
    return {"winner_cte": chosen, "switched": switched, "audit": audit}
