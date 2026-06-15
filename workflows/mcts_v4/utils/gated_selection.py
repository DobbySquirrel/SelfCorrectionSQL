"""Runtime gated R4→R8: R4 shortcut when clear; LLM pairwise only on ambiguous top clusters."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

import pandas as pd

from .confidence_core import (
    ConfidenceSelection,
    confidence_aware_selection,
    env_float,
    env_int,
    make_llm_from_config,
)
from .sql_selector import SQLSelector

ENV_CONFIDENCE_MODE = "MCTS_CONFIDENCE_MODE"
ENV_GATE_MARGIN = "MCTS_R4_GATE_MARGIN"
ENV_CONF_THRESHOLD = "MCTS_CONFIDENCE_THRESHOLD"
ENV_CONF_TOP_K = "MCTS_CONFIDENCE_TOP_K"
ENV_CONF_VOTES = "MCTS_CONFIDENCE_VOTE_SAMPLES"


@dataclass
class R4GateAnalysis:
    sql: str
    ambiguous: bool
    gate_reason: str
    ranked_votes: List[Tuple[str, int]]
    gate_sigs: List[str]


def confidence_mode_enabled(mode: Optional[str] = None) -> bool:
    raw = (mode if mode is not None else os.environ.get(ENV_CONFIDENCE_MODE, "")).strip().lower()
    return raw in ("gated", "1", "true", "yes", "on")


def _analyze_r4_gate(rss: List[Dict[str, Any]], vote_margin: float) -> R4GateAnalysis:
    from .r4_vote import collect_r4_cluster_votes

    clusters = SQLSelector._build_clusters(rss)
    votes = collect_r4_cluster_votes(rss)

    ranked = votes.most_common()
    if not ranked:
        return R4GateAnalysis(sql="", ambiguous=False, gate_reason="no_votes", ranked_votes=[], gate_sigs=[])

    top_v = ranked[0][1]
    tied_top = [sig for sig, v in ranked if v == top_v]
    r4_sql = SQLSelector._select_r4_majority_then_reward(rss)

    if len(tied_top) > 1:
        return R4GateAnalysis(
            sql=r4_sql,
            ambiguous=True,
            gate_reason="vote_tie",
            ranked_votes=ranked,
            gate_sigs=tied_top,
        )

    if len(ranked) >= 2 and ranked[1][1] >= vote_margin * top_v:
        return R4GateAnalysis(
            sql=r4_sql,
            ambiguous=True,
            gate_reason=f"margin>={vote_margin}",
            ranked_votes=ranked,
            gate_sigs=[ranked[0][0], ranked[1][0]],
        )

    return R4GateAnalysis(
        sql=r4_sql,
        ambiguous=False,
        gate_reason="clear",
        ranked_votes=ranked,
        gate_sigs=[ranked[0][0]],
    )


def _sqls_for_gate_sigs(clusters: Dict[str, Any], gate_sigs: List[str]) -> List[str]:
    seen: set = set()
    out: List[str] = []
    for sig in gate_sigs:
        c = clusters.get(sig)
        if not c:
            continue
        for sql, _, _ in c.variants:
            s = (sql or "").strip()
            if s and s not in seen:
                seen.add(s)
                out.append(s)
    return out


def gated_r4_r8_select(
    rollout_stats_list: List[Dict[str, Any]],
    *,
    question: str = "",
    schema_ddl: str = "",
    db_connector=None,
    llm_config: Optional[dict] = None,
) -> Tuple[str, dict]:
    rss = rollout_stats_list or []
    clusters = SQLSelector._build_clusters(rss)
    vote_margin = env_float(ENV_GATE_MARGIN, 0.7)
    conf_threshold = env_float(ENV_CONF_THRESHOLD, 0.7)
    top_k = env_int(ENV_CONF_TOP_K, 3)
    vote_samples = env_int(ENV_CONF_VOTES, 3)

    r4 = _analyze_r4_gate(rss, vote_margin)
    top_sig = r4.ranked_votes[0][0][:16] if r4.ranked_votes else "?"
    top_votes = r4.ranked_votes[0][1] if r4.ranked_votes else 0
    print(
        f"[Selection] R4: majority cluster sig={top_sig}… votes={top_votes}"
    )

    meta = {
        "mode": "r4_shortcut",
        "gate_reason": r4.gate_reason,
        "pairwise_calls": 0,
        "top_confidence": 1.0 if r4.gate_reason == "clear" else 0.0,
    }

    if not r4.ambiguous or not r4.sql:
        print(
            f"[Selection] R4+R8_gated: mode={meta['mode']} conf={meta['top_confidence']:.3f} "
            f"pairwise={meta['pairwise_calls']} gate={meta['gate_reason']}"
        )
        return r4.sql, meta

    gate_sqls = _sqls_for_gate_sigs(clusters, r4.gate_sigs)
    if len(gate_sqls) <= 1:
        meta["mode"] = "r4_gate_single"
        print(
            f"[Selection] R4+R8_gated: mode={meta['mode']} conf={meta['top_confidence']:.3f} "
            f"pairwise={meta['pairwise_calls']} gate={meta['gate_reason']}"
        )
        return r4.sql, meta

    if db_connector is None:
        meta["mode"] = "r4_ambiguous_no_db"
        print(
            f"[Selection] R4+R8_gated: mode={meta['mode']} conf={meta['top_confidence']:.3f} "
            f"pairwise={meta['pairwise_calls']} gate={meta['gate_reason']}"
        )
        return r4.sql, meta

    def exec_fn(sql: str) -> Tuple[Optional[pd.DataFrame], Optional[str]]:
        df, err = db_connector.execute_query(sql)
        return df, err

    llm_call = make_llm_from_config(llm_config) if llm_config else None
    sel: ConfidenceSelection = confidence_aware_selection(
        gate_sqls,
        question=question,
        schema=schema_ddl,
        execute_fn=exec_fn,
        threshold=conf_threshold,
        top_k=min(top_k, len(gate_sqls)),
        vote_samples=vote_samples,
        llm_call=llm_call,
        db_connector=db_connector,
    )
    mode = f"r8_{sel.mode}"
    meta.update(
        {
            "mode": mode,
            "pairwise_calls": sel.pairwise_calls,
            "top_confidence": sel.top_confidence,
        }
    )
    picked = (sel.sql or r4.sql).strip()
    print(
        f"[Selection] R4+R8_gated: mode={mode} conf={sel.top_confidence:.3f} "
        f"pairwise={sel.pairwise_calls} gate={r4.gate_reason}"
    )
    return picked, meta
