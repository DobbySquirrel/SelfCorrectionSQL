"""Execution-cluster confidence + optional LLM pairwise rerank (R8)."""

from __future__ import annotations

import hashlib
import json
import os
import random
import re
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Tuple

import pandas as pd

from .mcts_helpers import MCTSUtils

_PAIRWISE_SYSTEM = """You compare two SQLite SQL candidates for the same natural-language question.
Pick the candidate that better answers the question given the schema and execution samples.
Reply with exactly one token: A or B."""


@dataclass
class ConfidenceSelection:
    sql: str
    mode: str
    pairwise_calls: int
    top_confidence: float


def _norm_sql(sql: str) -> str:
    return " ".join((sql or "").split()).strip().lower()


def _rows_from_df(df: pd.DataFrame) -> list:
    if df is None:
        return []
    return df.to_dict("records")


def _result_signature(df: pd.DataFrame, err: Optional[str]) -> str:
    result = {
        "valid": err is None and df is not None,
        "query_result": _rows_from_df(df),
        "error": err,
    }
    return MCTSUtils.create_result_signature(result) or ""


def _result_hash(sig: str) -> str:
    if not sig:
        return ""
    return str(int(hashlib.md5(sig.encode("utf-8")).hexdigest()[:16], 16))


def _tiebreak_pick(
    rows: List[Tuple[str, int]],
    *,
    db_connector=None,
) -> str:
    if not rows:
        return ""
    from .execution_tiebreak import tiebreak_pick_variants

    variants = [(sql, 0.0, row_count) for sql, row_count in rows]
    return tiebreak_pick_variants(variants, db_connector=db_connector)


def _cluster_executed(
    sqls: List[str],
    execute_fn: Callable[[str], Tuple[Optional[pd.DataFrame], Optional[str]]],
    *,
    db_connector=None,
) -> List[dict]:
    by_sig: Dict[str, dict] = {}
    for sql in sqls:
        s = (sql or "").strip()
        if not s:
            continue
        df, err = execute_fn(s)
        sig = _result_signature(df, err)
        if not sig:
            continue
        c = by_sig.setdefault(
            sig,
            {"sig": sig, "hash": _result_hash(sig), "variants": [], "size": 0},
        )
        rows = len(df) if df is not None and err is None else 0
        c["variants"].append((s, rows))
        c["size"] += 1
    clusters = sorted(by_sig.values(), key=lambda x: (-x["size"], x["hash"]))
    for c in clusters:
        c["rep_sql"] = _tiebreak_pick(c["variants"], db_connector=db_connector)
        c["consistency"] = c["size"] / max(1, len([x for x in sqls if (x or "").strip()]))
    return clusters


def _bootstrap_confidence(clusters: List[dict], n_sqls: int, vote_samples: int) -> float:
    if not clusters or n_sqls <= 0:
        return 0.0
    if vote_samples <= 1:
        return clusters[0]["size"] / n_sqls
    reps: List[str] = []
    for c in clusters:
        reps.extend([c["sig"]] * c["size"])
    if not reps:
        return 0.0
    scores: List[float] = []
    rng = random.Random(0)
    for _ in range(vote_samples):
        sample = [rng.choice(reps) for _ in range(n_sqls)]
        top = max(set(sample), key=sample.count)
        scores.append(sample.count(top) / n_sqls)
    return sum(scores) / len(scores)


def _pairwise_pick(
    candidates: List[str],
    *,
    question: str,
    schema: str,
    execute_fn: Callable[[str], Tuple[Optional[pd.DataFrame], Optional[str]]],
    llm_call: Callable[[str], str],
    vote_samples: int,
) -> Tuple[str, int]:
    if not candidates:
        return "", 0
    if len(candidates) == 1:
        return candidates[0], 0

    def _exec_preview(sql: str) -> str:
        df, err = execute_fn(sql)
        if err or df is None:
            return f"error: {err or 'failed'}"
        rows = _rows_from_df(df)
        preview = rows[:3]
        return f"rows={len(rows)} preview={json.dumps(preview, ensure_ascii=False)[:400]}"

    wins: Dict[str, int] = {c: 0 for c in candidates}
    calls = 0
    for i in range(len(candidates)):
        for j in range(i + 1, len(candidates)):
            a, b = candidates[i], candidates[j]
            for _ in range(max(1, vote_samples)):
                prompt = (
                    f"{_PAIRWISE_SYSTEM}\n\n"
                    f"Question: {question}\n"
                    f"Schema (truncated):\n{(schema or '')[:6000]}\n\n"
                    f"Candidate A:\n{a}\nExec A: {_exec_preview(a)}\n\n"
                    f"Candidate B:\n{b}\nExec B: {_exec_preview(b)}\n\n"
                    "Answer A or B only.\n/no_think"
                )
                try:
                    resp = (llm_call(prompt) or "").strip()
                except Exception:
                    resp = ""
                calls += 1
                pick = None
                m = re.search(r"\b([AB])\b", resp.upper())
                if m:
                    pick = m.group(1)
                elif "A" in resp.upper() and "B" not in resp.upper():
                    pick = "A"
                elif "B" in resp.upper() and "A" not in resp.upper():
                    pick = "B"
                if pick == "A":
                    wins[a] += 1
                elif pick == "B":
                    wins[b] += 1
                else:
                    wins[a] += 1

    best = max(candidates, key=lambda s: (wins.get(s, 0), -len(s)))
    return best, calls


def make_openai_caller(base_url: str, model: str, *, api_key: str = "EMPTY") -> Callable[[str], str]:
    from openai import OpenAI

    client = OpenAI(base_url=base_url, api_key=api_key)

    def _call(prompt: str) -> str:
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
            max_tokens=32,
        )
        return (resp.choices[0].message.content or "").strip()

    return _call


def make_llm_from_config(llm_config: dict) -> Callable[[str], str]:
    configs = list(llm_config.get("config_list") or [{}])
    if not configs:
        configs = [{}]
    idx = {"i": 0}

    def _call(prompt: str) -> str:
        c = configs[idx["i"] % len(configs)]
        idx["i"] += 1
        fn = make_openai_caller(
            c.get("base_url") or "http://127.0.0.1:8000/v1",
            c.get("model") or "",
            api_key=c.get("api_key") or "EMPTY",
        )
        return fn(prompt)

    return _call


def collect_sqls_from_record(rec: dict) -> List[str]:
    seen: set = set()
    out: List[str] = []
    for rs in rec.get("rollout_stats") or []:
        for v in rs.get("all_sql_variants") or []:
            s = (v.get("sql") or "").strip()
            if s and s not in seen:
                seen.add(s)
                out.append(s)
        sel = (rs.get("selected_sql") or "").strip()
        if sel and sel not in seen:
            seen.add(sel)
            out.append(sel)
    return out


def confidence_aware_selection(
    sqls: List[str],
    *,
    question: str,
    schema: str,
    execute_fn: Callable[[str], Tuple[Optional[pd.DataFrame], Optional[str]]],
    threshold: float = 0.7,
    top_k: int = 3,
    vote_samples: int = 3,
    llm_call: Optional[Callable[[str], str]] = None,
    db_connector=None,
) -> ConfidenceSelection:
    uniq = list(dict.fromkeys((s or "").strip() for s in sqls if (s or "").strip()))
    if not uniq:
        return ConfidenceSelection(sql="", mode="empty", pairwise_calls=0, top_confidence=0.0)
    if len(uniq) == 1:
        return ConfidenceSelection(sql=uniq[0], mode="single", pairwise_calls=0, top_confidence=1.0)

    clusters = _cluster_executed(uniq, execute_fn, db_connector=db_connector)
    if not clusters:
        return ConfidenceSelection(sql=uniq[0], mode="no_exec", pairwise_calls=0, top_confidence=0.0)

    top_conf = _bootstrap_confidence(clusters, len(uniq), vote_samples)
    reps = [c["rep_sql"] for c in clusters[: max(1, top_k)]]

    if top_conf >= threshold:
        return ConfidenceSelection(
            sql=clusters[0]["rep_sql"],
            mode="shortcut",
            pairwise_calls=0,
            top_confidence=top_conf,
        )

    if llm_call is None:
        return ConfidenceSelection(
            sql=clusters[0]["rep_sql"],
            mode="low_conf_fallback",
            pairwise_calls=0,
            top_confidence=top_conf,
        )

    picked, calls = _pairwise_pick(
        reps,
        question=question,
        schema=schema,
        execute_fn=execute_fn,
        llm_call=llm_call,
        vote_samples=1,
    )
    return ConfidenceSelection(
        sql=picked or clusters[0]["rep_sql"],
        mode="pairwise",
        pairwise_calls=calls,
        top_confidence=top_conf,
    )


def env_float(name: str, default: float) -> float:
    raw = os.environ.get(name, str(default)).strip()
    try:
        return float(raw)
    except ValueError:
        return default


def env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, str(default)).strip()
    try:
        return int(raw)
    except ValueError:
        return default
