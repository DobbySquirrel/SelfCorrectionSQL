#!/usr/bin/env python3
"""Offline gated R4→R8: R4 shortcut when clear; LLM pairwise only on ambiguous top clusters."""

from __future__ import annotations

import argparse
import io
import json
import sys
import threading
from collections import Counter
from contextlib import redirect_stdout
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[7]
ANALYSIS = ROOT / "workflows/mcts_v4/test/out/cte_diverse/analysis"
DRYRUN = ANALYSIS / "cte_checker_dryrun"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "workflows/mcts_v4/test/out/clarify_a0_a2_coder/analysis/parallel_during_rerun"))
sys.path.insert(0, str(ANALYSIS / "confidence_selection"))
sys.path.insert(0, str(ANALYSIS))
sys.path.insert(0, str(DRYRUN))

from common.timed_sql import (  # noqa: E402
    DEFAULT_DB_WORKERS,
    DEFAULT_SQL_TIMEOUT,
    execute_timed_batch,
    prefetch_gold_matches,
)
from confidence_core import confidence_aware_selection, make_openai_caller  # noqa: E402
from llm_revision import make_round_robin_caller  # noqa: E402
from selector_replay import Cluster, _tiebreak_pick, build_clusters, select_sql  # noqa: E402

GOLD = ROOT / "workflows/mcts_v3/data/arcwise_plat_sql_only_with_diff_withSchema.json"
PPL = ROOT / "workflows/mcts_v1/test/out/arcwise_alpha_sql_ppl_rollouts_20_3_15_nigga.json"
HERE = Path(__file__).resolve().parent
DEFAULT_FILTER = ROOT / "workflows/mcts_v4/test/out/cte_diverse/v4_checker_filter_498_rollouts12.json"


@dataclass
class R4Analysis:
    sql: str
    ambiguous: bool
    gate_reason: str
    ranked_votes: List[Tuple[str, int]]
    gate_sigs: List[str]


def label_map(rec: dict) -> dict:
    return {
        (a.get("sql") or "").strip(): bool(a.get("is_correct"))
        for a in (rec.get("all_sqls_with_attributes") or [])
        if (a.get("sql") or "").strip()
    }


def hit_from_labels(rec: dict, sql: str) -> bool:
    if not sql:
        return False
    return bool(label_map(rec).get(sql.strip()))


def pick_r4_sql(rss: List[dict], clusters: Dict[str, Cluster]) -> str:
    return (select_sql("R4_majority_then_reward", rss) or "").strip()


def analyze_r4_gate(rss: List[dict], vote_margin: float) -> R4Analysis:
    from workflows.mcts_v4.utils.r4_vote import collect_r4_cluster_votes

    clusters = build_clusters(rss)
    votes = collect_r4_cluster_votes(rss)

    ranked = votes.most_common()
    if not ranked:
        return R4Analysis(sql="", ambiguous=False, gate_reason="no_votes", ranked_votes=[], gate_sigs=[])

    top_v = ranked[0][1]
    tied_top = [sig for sig, v in ranked if v == top_v]
    sql = pick_r4_sql(rss, clusters)

    if len(tied_top) > 1:
        return R4Analysis(
            sql=sql,
            ambiguous=True,
            gate_reason="vote_tie",
            ranked_votes=ranked,
            gate_sigs=tied_top,
        )

    if len(ranked) >= 2 and ranked[1][1] >= vote_margin * top_v:
        return R4Analysis(
            sql=sql,
            ambiguous=True,
            gate_reason=f"margin>={vote_margin}",
            ranked_votes=ranked,
            gate_sigs=[ranked[0][0], ranked[1][0]],
        )

    return R4Analysis(
        sql=sql,
        ambiguous=False,
        gate_reason="clear",
        ranked_votes=ranked,
        gate_sigs=[ranked[0][0]],
    )


def sqls_for_gate_sigs(clusters: Dict[str, Cluster], gate_sigs: List[str]) -> List[str]:
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
    rec: dict,
    *,
    question: str,
    schema: str,
    exec_fn: Callable[[str], tuple],
    llm_call: Callable[[str], str],
    vote_margin: float,
    conf_threshold: float,
    vote_samples: int,
) -> dict:
    rss = rec.get("rollout_stats") or []
    clusters = build_clusters(rss)
    r4 = analyze_r4_gate(rss, vote_margin)

    if not r4.ambiguous or not r4.sql:
        return {
            "sql": r4.sql,
            "mode": "r4_shortcut",
            "gate_reason": r4.gate_reason,
            "pairwise_calls": 0,
            "top_confidence": 1.0 if r4.gate_reason == "clear" else 0.0,
            "n_gate_sqls": 0,
        }

    gate_sqls = sqls_for_gate_sigs(clusters, r4.gate_sigs)
    if len(gate_sqls) <= 1:
        return {
            "sql": r4.sql,
            "mode": "r4_gate_single",
            "gate_reason": r4.gate_reason,
            "pairwise_calls": 0,
            "top_confidence": 0.0,
            "n_gate_sqls": len(gate_sqls),
        }

    sel = confidence_aware_selection(
        gate_sqls,
        question=question,
        schema=schema,
        execute_fn=exec_fn,
        threshold=conf_threshold,
        top_k=min(3, len(gate_sqls)),
        vote_samples=vote_samples,
        llm_call=llm_call,
    )
    return {
        "sql": (sel.sql or r4.sql).strip(),
        "mode": f"r8_{sel.mode}",
        "gate_reason": r4.gate_reason,
        "pairwise_calls": sel.pairwise_calls,
        "top_confidence": sel.top_confidence,
        "n_gate_sqls": len(gate_sqls),
    }


def merge_rec(a: dict, b: dict) -> dict:
    attrs: dict = {}
    for rec in (a, b):
        for x in rec.get("all_sqls_with_attributes") or []:
            sql = (x.get("sql") or "").strip()
            if sql:
                attrs[sql] = x
    return {
        "rollout_stats": (a.get("rollout_stats") or []) + (b.get("rollout_stats") or []),
        "all_sqls_with_attributes": list(attrs.values()),
    }


def make_llm(base_urls: str, model: str) -> Callable[[str], str]:
    urls = [u.strip() for u in base_urls.split(",") if u.strip()]
    if len(urls) > 1:
        return make_round_robin_caller(urls, model=model)
    return make_openai_caller(urls[0] if urls else "http://127.0.0.1:8000/v1", model)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", type=Path, default=DEFAULT_FILTER)
    ap.add_argument("--filter", type=Path, default=None)
    ap.add_argument("--rev", type=Path, default=None)
    ap.add_argument("--vote-margin", type=float, default=0.85, help="Gate when #2 votes >= margin * #1")
    ap.add_argument("--conf-threshold", type=float, default=0.7)
    ap.add_argument("--votes", type=int, default=3)
    ap.add_argument("--base-urls", default="http://127.0.0.1:8000/v1,http://127.0.0.1:8100/v1")
    ap.add_argument("--model", default="/hpc2hdd/home/sshen190/wtao565/models/Qwen3-Coder-30B")
    ap.add_argument("--output-json", type=Path, default=None)
    ap.add_argument("--output-md", type=Path, default=None)
    ap.add_argument("--resume", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    if args.filter and args.rev:
        filt = json.loads(args.filter.read_text(encoding="utf-8"))
        rev = json.loads(args.rev.read_text(encoding="utf-8"))
        data = {q: merge_rec(filt.get(q, {}), rev.get(q, {})) for q in sorted(set(filt) | set(rev), key=int)}
        arm = "merged_filter_rev"
    else:
        data = json.loads(args.input.read_text(encoding="utf-8"))
        arm = args.input.stem.replace("_rollouts12", "")

    tag = f"margin{args.vote_margin}"
    out_json = args.output_json or HERE / f"gated_r4_r8_{arm}_{tag}.json"
    out_md = args.output_md or HERE / f"gated_r4_r8_{arm}_{tag}.md"

    gold_items = json.loads(GOLD.read_text(encoding="utf-8"))
    gold = {str(x["question_id"]): x["SQL"] for x in gold_items}
    qdb = {str(x["question_id"]): x.get("db", "") for x in json.loads(PPL.read_text(encoding="utf-8"))}
    qschema = {str(x["question_id"]): (x.get("schema_prompt") or x.get("ddl_data") or "") for x in gold_items}
    qq = {str(x["question_id"]): x.get("question", "") for x in gold_items}

    qids = sorted(data.keys(), key=lambda x: int(x))
    if args.limit:
        qids = qids[: args.limit]

    done: Dict[str, dict] = {}
    if args.resume and out_json.is_file():
        for row in json.loads(out_json.read_text(encoding="utf-8")).get("per_question", []):
            done[str(row["qid"])] = row

    llm = make_llm(args.base_urls, args.model)
    memo: dict = {}
    gold_cache: dict = {}

    for i, qid in enumerate(qids, 1):
        if qid in done:
            continue
        rec = data[qid]
        rss = rec.get("rollout_stats") or []
        with redirect_stdout(io.StringIO()):
            r4_sql = pick_r4_sql(rss, build_clusters(rss))
        r4_ok = hit_from_labels(rec, r4_sql)

        all_sqls = list(
            dict.fromkeys(
                (v.get("sql") or "").strip()
                for rs in rss
                for v in rs.get("all_sql_variants") or []
                if (v.get("sql") or "").strip()
            )
        )
        db_id = qdb.get(qid, "")
        exec_batch = execute_timed_batch(db_id, all_sqls, timeout_s=DEFAULT_SQL_TIMEOUT, workers=DEFAULT_DB_WORKERS)

        def exec_fn(sql, _batch=exec_batch):
            key = " ".join((sql or "").split()).strip().lower()
            df, err = _batch.get(key, (None, "missing"))
            if err or df is None:
                return None, str(err or "missing")
            return df, None

        sel = gated_r4_r8_select(
            rec,
            question=qq.get(qid, ""),
            schema=qschema.get(qid, ""),
            exec_fn=exec_fn,
            llm_call=llm,
            vote_margin=args.vote_margin,
            conf_threshold=args.conf_threshold,
            vote_samples=args.votes,
        )
        gated_sql = sel["sql"]
        gated_ok = hit_from_labels(rec, gated_sql)
        if not gated_ok and gated_sql not in label_map(rec):
            pool = list(dict.fromkeys(all_sqls + [gated_sql]))
            prefetch_gold_matches(db_id, gold.get(qid, ""), pool, memo, gold_cache=gold_cache)
            gated_ok = memo.get((db_id, gated_sql), False)

        done[qid] = {
            "qid": qid,
            "r4_ok": r4_ok,
            "gated_ok": gated_ok,
            "mode": sel["mode"],
            "gate_reason": sel["gate_reason"],
            "pairwise_calls": sel["pairwise_calls"],
            "n_gate_sqls": sel["n_gate_sqls"],
            "top_confidence": round(float(sel["top_confidence"]), 4),
            "gated_sql": gated_sql,
        }
        if i % 5 == 0 or i == len(qids):
            r4h = sum(1 for r in done.values() if r["r4_ok"])
            gh = sum(1 for r in done.values() if r["gated_ok"])
            gated_n = sum(1 for r in done.values() if r["mode"].startswith("r8_"))
            pw = sum(int(r.get("pairwise_calls") or 0) for r in done.values())
            print(
                f"[{i}/{len(qids)}] R4={r4h}/{len(done)} gated={gh}/{len(done)} "
                f"llm_gated={gated_n} pairwise={pw}",
                flush=True,
            )
            out_json.write_text(
                json.dumps(
                    {
                        "meta": {
                            "arm": arm,
                            "vote_margin": args.vote_margin,
                            "conf_threshold": args.conf_threshold,
                            "base_urls": args.base_urls,
                            "n": len(done),
                        },
                        "per_question": list(done.values()),
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )

    rows = [done[k] for k in sorted(done.keys(), key=lambda x: int(x))]
    n = len(rows)
    r4h = sum(1 for r in rows if r["r4_ok"])
    gh = sum(1 for r in rows if r["gated_ok"])
    imp = sum(1 for r in rows if r["gated_ok"] and not r["r4_ok"])
    hurt = sum(1 for r in rows if r["r4_ok"] and not r["gated_ok"])
    pw = sum(int(r.get("pairwise_calls") or 0) for r in rows)
    gated_n = sum(1 for r in rows if r["mode"].startswith("r8_"))
    shortcut_n = sum(1 for r in rows if r["mode"] == "r4_shortcut")

    by_reason: Dict[str, int] = {}
    for r in rows:
        by_reason[r["gate_reason"]] = by_reason.get(r["gate_reason"], 0) + 1

    md = [
        f"# Gated R4→R8 offline — {arm}",
        "",
        f"Generated: {datetime.now(timezone.utc).isoformat()}",
        f"vote_margin: **{args.vote_margin}** (gate when #2 votes ≥ margin × #1, or vote tie)",
        f"conf_threshold: {args.conf_threshold}",
        f"base_urls: `{args.base_urls}`",
        "",
        "| 指标 | 值 |",
        "|---|---:|",
        f"| Hit@1 R4 | {r4h}/{n} |",
        f"| Hit@1 gated R4→R8 | {gh}/{n} |",
        f"| Δ | {gh - r4h:+d} |",
        f"| improved / hurt | {imp} / {hurt} |",
        f"| R4 shortcut (no LLM) | {shortcut_n}/{n} |",
        f"| LLM gated (r8_*) | {gated_n}/{n} |",
        f"| pairwise calls | {pw} |",
        "",
        "### Gate 触发分布",
        "",
        "| reason | count |",
        "|---|---:|",
    ]
    for reason, cnt in sorted(by_reason.items(), key=lambda x: -x[1]):
        md.append(f"| {reason} | {cnt} |")

    out_md.write_text("\n".join(md) + "\n", encoding="utf-8")
    print("\n".join(md[-12:]))
    print(f"Wrote {out_json} and {out_md}")


if __name__ == "__main__":
    main()
