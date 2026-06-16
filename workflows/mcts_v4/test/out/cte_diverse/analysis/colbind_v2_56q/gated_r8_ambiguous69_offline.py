#!/usr/bin/env python3
"""Offline gated R4→R8 on sigA nomin2 ambiguous cohort (production selectors + LLM pairwise).

Compares:
  - prod_r4: SQLSelector mul_purity + ambig_purity (bprime defaults)
  - gated_r8: gated_r4_r8_select (MCTS_CONFIDENCE_MODE=gated path)
"""

from __future__ import annotations

import argparse
import io
import json
import os
import sys
from contextlib import redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List

ROOT = Path(__file__).resolve().parents[7]
sys.path.insert(0, str(ROOT))

from workflows.mcts_v4.utils.confidence_core import make_openai_caller  # noqa: E402
from workflows.mcts_v4.utils.gated_selection import gated_r4_r8_select  # noqa: E402
from workflows.mcts_v4.utils.sql_selector import SQLSelector  # noqa: E402

HERE = Path(__file__).resolve().parent
DEFAULT_INPUT = (
    ROOT
    / "workflows/mcts_v4/test/out/cte_diverse/v4_colbind_v2_dual03_abl5_sigA_nomin2_full498_r12.json"
)
DEFAULT_MANIFEST = HERE / "qids_sigA_nomin2_ambiguous69_manifest.json"
GOLD = ROOT / "workflows/mcts_v3/data/arcwise_plat_sql_only_with_diff_withSchema.json"
PPL = ROOT / "workflows/mcts_v1/test/out/arcwise_alpha_sql_ppl_rollouts_20_3_15_nigga.json"


def label_map(rec: dict) -> dict:
    return {
        (a.get("sql") or "").strip(): bool(a.get("is_correct"))
        for a in (rec.get("all_sqls_with_attributes") or [])
        if (a.get("sql") or "").strip()
    }


def hit_sql(rec: dict, sql: str) -> bool:
    return bool(label_map(rec).get((sql or "").strip()))


def make_llm(base_urls: str, model: str):
    urls = [u.strip() for u in base_urls.split(",") if u.strip()]
    if len(urls) > 1:
        idx = {"i": 0}

        def _rr(prompt: str) -> str:
            url = urls[idx["i"] % len(urls)]
            idx["i"] += 1
            return make_openai_caller(url, model)(prompt)

        return _rr
    return make_openai_caller(urls[0] if urls else "http://127.0.0.1:8000/v1", model)


def pick_prod_r4(rec: dict, db_connector) -> str:
    rss = rec.get("rollout_stats") or []
    with redirect_stdout(io.StringIO()):
        return (
            SQLSelector._select_r4_majority_then_reward(
                rss,
                db_connector=db_connector,
                question="",
                schema_ddl="",
            )
            or ""
        ).strip()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    ap.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    ap.add_argument("--vote-margin", type=float, default=0.7)
    ap.add_argument("--conf-threshold", type=float, default=0.7)
    ap.add_argument("--votes", type=int, default=3)
    ap.add_argument("--base-urls", default="http://127.0.0.1:8000/v1,http://127.0.0.1:8100/v1")
    ap.add_argument("--model", default="/hpc2hdd/home/sshen190/wtao565/models/Qwen3-Coder-30B")
    ap.add_argument("--output-json", type=Path, default=None)
    ap.add_argument("--output-md", type=Path, default=None)
    ap.add_argument("--resume", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    tag = f"margin{args.vote_margin}"
    out_json = args.output_json or HERE / f"gated_r8_sigA_nomin2_ambig69_{tag}.json"
    out_md = args.output_md or HERE / f"gated_r8_sigA_nomin2_ambig69_{tag}.md"

    pool = json.loads(args.input.read_text(encoding="utf-8"))
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    qids: List[str] = [str(q) for q in manifest.get("qids") or []]
    if args.limit:
        qids = qids[: args.limit]

    gold_items = json.loads(GOLD.read_text(encoding="utf-8"))
    qdb = {str(x["question_id"]): x.get("db", "") for x in json.loads(PPL.read_text(encoding="utf-8"))}
    qschema = {
        str(x["question_id"]): (x.get("schema_prompt") or x.get("ddl_data") or "") for x in gold_items
    }
    qq = {str(x["question_id"]): x.get("question", "") for x in gold_items}

    from workflows.mcts_v1.test.test_mcts import build_db_connector  # noqa: E402

    os.environ.setdefault("MCTS_R4_SCORE_MODE", "mul_purity")
    os.environ.setdefault("MCTS_R4_TOPK_BOOTSTRAP", "ambig_purity")
    os.environ["MCTS_R4_GATE_MARGIN"] = str(args.vote_margin)
    os.environ["MCTS_CONFIDENCE_THRESHOLD"] = str(args.conf_threshold)
    os.environ["MCTS_CONFIDENCE_VOTE_SAMPLES"] = str(args.votes)

    done: Dict[str, dict] = {}
    if args.resume and out_json.is_file():
        prev = json.loads(out_json.read_text(encoding="utf-8"))
        for row in prev.get("per_question") or []:
            done[str(row["qid"])] = row

    llm = make_llm(args.base_urls, args.model)
    llm_config = {
        "config_list": [
            {"base_url": u.strip(), "model": args.model, "api_key": "EMPTY"}
            for u in args.base_urls.split(",")
            if u.strip()
        ]
    }

    for i, qid in enumerate(qids, 1):
        if qid in done:
            continue
        rec = pool[qid]
        db_id = qdb.get(qid, "")
        conn = build_db_connector(db_id) if db_id else None
        try:
            prod_sql = pick_prod_r4(rec, conn)
            prod_ok = hit_sql(rec, prod_sql)

            meta = {}
            gated_sql = prod_sql
            if conn is not None:
                with redirect_stdout(io.StringIO()):
                    gated_sql, meta = gated_r4_r8_select(
                        rec.get("rollout_stats") or [],
                        question=qq.get(qid, ""),
                        schema_ddl=qschema.get(qid, ""),
                        db_connector=conn,
                        llm_config=llm_config,
                    )
                gated_sql = (gated_sql or prod_sql).strip()
            gated_ok = hit_sql(rec, gated_sql)

            per = manifest.get("per_qid") or []
            gate_reason = next((r.get("gate_reason") for r in per if str(r.get("qid")) == qid), "?")

            done[qid] = {
                "qid": qid,
                "gate_reason": gate_reason,
                "prod_r4_ok": prod_ok,
                "gated_r8_ok": gated_ok,
                "prod_r4_sql": prod_sql,
                "gated_r8_sql": gated_sql,
                "mode": meta.get("mode", ""),
                "pairwise_calls": int(meta.get("pairwise_calls") or 0),
                "top_confidence": round(float(meta.get("top_confidence") or 0.0), 4),
            }
        finally:
            if conn is not None:
                try:
                    conn.disconnect()
                except Exception:
                    pass

        if i % 3 == 0 or i == len(qids):
            pr = sum(1 for r in done.values() if r["prod_r4_ok"])
            gr = sum(1 for r in done.values() if r["gated_r8_ok"])
            pw = sum(int(r.get("pairwise_calls") or 0) for r in done.values())
            print(f"[{i}/{len(qids)}] prod_r4={pr}/{len(done)} gated_r8={gr}/{len(done)} pairwise={pw}", flush=True)
            out_json.write_text(
                json.dumps(
                    {
                        "meta": {
                            "input": str(args.input),
                            "manifest": str(args.manifest),
                            "vote_margin": args.vote_margin,
                            "n": len(done),
                            "base_urls": args.base_urls,
                        },
                        "per_question": [done[k] for k in sorted(done.keys(), key=int)],
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )

    rows = [done[k] for k in sorted(done.keys(), key=int)]
    n = len(rows)
    pr = sum(1 for r in rows if r["prod_r4_ok"])
    gr = sum(1 for r in rows if r["gated_r8_ok"])
    imp = sum(1 for r in rows if r["gated_r8_ok"] and not r["prod_r4_ok"])
    hurt = sum(1 for r in rows if r["prod_r4_ok"] and not r["gated_r8_ok"])
    pw = sum(int(r.get("pairwise_calls") or 0) for r in rows)
    r8_n = sum(1 for r in rows if str(r.get("mode", "")).startswith("r8_"))

    md = [
        f"# Gated R4→R8 offline — sigA nomin2 ambiguous cohort",
        "",
        f"Generated: {datetime.now(timezone.utc).isoformat()}",
        f"manifest: `{args.manifest.name}` (n={manifest.get('n_ambiguous', n)})",
        f"vote_margin: **{args.vote_margin}**",
        f"base_urls: `{args.base_urls}`",
        "",
        "| 指标 | 值 |",
        "|---|---:|",
        f"| cohort n | {n} |",
        f"| Hit@1 prod R4 (mul_purity+ambig_purity) | {pr}/{n} |",
        f"| Hit@1 gated R4→R8 | {gr}/{n} |",
        f"| Δ gated vs prod | {gr - pr:+d} |",
        f"| improved / hurt | {imp} / {hurt} |",
        f"| r8_* modes | {r8_n}/{n} |",
        f"| pairwise calls | {pw} |",
    ]
    out_md.write_text("\n".join(md) + "\n", encoding="utf-8")
    print("\n".join(md[-8:]))
    print(f"Wrote {out_json} and {out_md}")


if __name__ == "__main__":
    main()
