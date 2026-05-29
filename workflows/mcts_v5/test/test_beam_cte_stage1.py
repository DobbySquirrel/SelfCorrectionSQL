#!/usr/bin/env python3
"""
Beam-CTE stage 1: form-enumerated A-axis + oneshot B–E + LLM judge rerank.

Usage (repo root):
  python workflows/mcts_v5/test/test_beam_cte_stage1.py \\
    --qids-file workflows/mcts_v5/test/fixtures/qids_30sample.json \\
    --k-a 3 \\
    --out workflows/mcts_v5/test/out/beam_cte_stage1.json \\
    2>&1 | tee workflows/mcts_v5/test/out/beam_cte_stage1.log
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

_REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO))

try:
    from dotenv import load_dotenv

    _env = _REPO / ".env"
    if _env.exists():
        load_dotenv(_env)
except ImportError:
    pass

from workflows.mcts_v1.core.database_connector import DatabaseConnector
from workflows.mcts_v5.llm import create_llm
from workflows.mcts_v5.mcts_workflow import TaxonomyMCTSWorkflow
from workflows.mcts_v5.utils.dataset import DEFAULT_ARCWISE_DATA, load_samples
from workflows.mcts_v5.utils.eval_ex import default_bird_dev_gold_path, load_gold_sql_map, resolve_gold_sql

from workflows.mcts_v5.test.beam_cte.a_axis_generator import (
    generate_axis_a_candidates,
    select_topk_axis_a,
)
from workflows.mcts_v5.test.beam_cte.beam_runner import run_beam_a_oneshot_rest
from workflows.mcts_v5.test.beam_cte.llm_judge import llm_judge_rerank
from workflows.mcts_v5.test.beam_cte.llm_stats import CountingLLM
from workflows.mcts_v5.test.test_taxonomy_prompt_lab import (
    LabCase,
    _connect_workflow_db,
    case_from_qid,
    load_qids_from_file,
    summarize_qid_ex,
)

REQUIRED_QIDS = {
    "17",
    "248",
    "371",
    "408",
    "480",
    "529",
    "738",
    "1166",
    "1257",
    "1427",
}


def _load_qids(path: Path) -> List[str]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict) and "qids" in data:
        qids = [str(x).strip() for x in data["qids"] if str(x).strip()]
    else:
        qids = load_qids_from_file(path)
    missing = sorted(REQUIRED_QIDS - set(qids))
    if missing:
        raise ValueError(f"qids file missing required qids: {missing}")
    return qids


def _collapse_stats(raw_a_list: List[Any]) -> Dict[str, int]:
    """Per-question A-axis probe_hash diversity."""
    hashes = [
        c.probe_hash
        for c in raw_a_list
        if c.probe_hash and c.is_valid and not (c.error or "").startswith("duplicate")
    ]
    unique = len(set(hashes))
    if unique == 0:
        return {"unique_hashes": 0, "class": "all_invalid"}
    if unique == 1:
        return {"unique_hashes": 1, "class": "all_same"}
    if unique == 2:
        return {"unique_hashes": 2, "class": "partial"}
    return {"unique_hashes": unique, "class": "all_different"}


def run_smoke_a_axis(chat_llm, wf, qid: str, data_file: Path) -> None:
    case = case_from_qid(qid, data_file, expand_at_depth=0)
    raw = generate_axis_a_candidates(
        chat_llm,
        question=case.question,
        evidence=case.evidence,
        schema_text=case.schema_prompt,
        db_executor=wf.sql_executor,
    )
    ranked = select_topk_axis_a(
        chat_llm,
        question=case.question,
        evidence=case.evidence,
        schema_text=case.schema_prompt,
        candidates=raw,
        k=3,
    )
    print(f"\n== Smoke A-axis qid={qid} ==")
    for c in raw:
        print(
            f"  {c.form_tag}: valid={c.is_valid} hash={str(c.probe_hash)[:16]} "
            f"err={c.error} sql={((c.cte_sql or '')[:80]).replace(chr(10), ' ')}"
        )
    print(f"  ranked: {[c.form_tag for c in ranked]}")
    stats = _collapse_stats(raw)
    print(f"  collapse: {stats}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Beam-CTE stage 1 evaluation")
    parser.add_argument(
        "--qids-file",
        type=str,
        default="workflows/mcts_v5/test/fixtures/qids_30sample.json",
    )
    parser.add_argument("--data-file", type=str, default=str(DEFAULT_ARCWISE_DATA))
    parser.add_argument("--gold-file", type=str, default="")
    parser.add_argument("--k-a", type=int, default=3)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--out", type=str, default="workflows/mcts_v5/test/out/beam_cte_stage1.json")
    parser.add_argument("--llm-backend", choices=("local", "yizhan"), default="local")
    parser.add_argument("--llm-preset", type=str, default="")
    parser.add_argument("--k-samples", type=int, default=5)
    parser.add_argument("--k-regen", type=int, default=3)
    parser.add_argument(
        "--smoke-qids",
        type=str,
        default="",
        help="Comma-separated qids for A-axis-only smoke (e.g. 371,529)",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    data_file = Path(args.data_file)
    qid_path = Path(args.qids_file)
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    if args.smoke_qids.strip():
        qids_smoke = [x.strip() for x in args.smoke_qids.split(",") if x.strip()]
    else:
        qids_smoke = []

    if not qid_path.is_file() and not qids_smoke:
        print(f"Qids file not found: {qid_path}")
        sys.exit(1)

    qids = _load_qids(qid_path) if qid_path.is_file() else []
    if args.limit > 0:
        qids = qids[: args.limit]

    if args.dry_run:
        print(f"Dry run: would evaluate {len(qids)} qids, k_a={args.k_a}")
        return

    inner_llm = create_llm(args.llm_backend, preset=args.llm_preset or None)
    chat_llm = CountingLLM(inner_llm)

    gold_path = Path(args.gold_file) if args.gold_file else default_bird_dev_gold_path()
    gold_map = load_gold_sql_map(gold_path) if gold_path.is_file() else {}

    wf: Optional[TaxonomyMCTSWorkflow] = None
    wf_db = ""

    if qids_smoke:
        for qid in qids_smoke:
            if wf is None or wf_db != case_from_qid(qid, data_file, 0).db:
                case_tmp = case_from_qid(qid, data_file, 0)
                if wf and wf.db_connector:
                    try:
                        wf.db_connector.disconnect()
                    except Exception:
                        pass
                wf_db = case_tmp.db
                wf = TaxonomyMCTSWorkflow.from_llm(
                    chat_llm,
                    DatabaseConnector(case_tmp.db),
                    k_samples=args.k_samples,
                    k_regen_per_node=args.k_regen,
                    collect_stats_on_node_creation=False,
                )
            if wf and _connect_workflow_db(wf):
                run_smoke_a_axis(chat_llm, wf, qid, data_file)
        return

    results: List[dict] = []
    collapse_rows: List[dict] = []
    t0_all = time.time()

    for qi, qid in enumerate(qids, 1):
        case = case_from_qid(qid, data_file, expand_at_depth=0)
        if wf is None or wf_db != case.db:
            if wf and wf.db_connector:
                try:
                    wf.db_connector.disconnect()
                except Exception:
                    pass
            wf_db = case.db
            wf = TaxonomyMCTSWorkflow.from_llm(
                chat_llm,
                DatabaseConnector(case.db),
                k_samples=args.k_samples,
                k_regen_per_node=args.k_regen,
                collect_stats_on_node_creation=False,
            )
        if not wf or not _connect_workflow_db(wf):
            results.append({"qid": qid, "error": "db_connect_failed"})
            continue

        calls_before = chat_llm.llm_calls_total
        tokens_before = chat_llm.tokens_total
        t0 = time.time()

        sample = {"question_id": qid, "db": case.db}
        gold_sql = resolve_gold_sql(sample, gold_map)

        raw_a = generate_axis_a_candidates(
            chat_llm,
            question=case.question,
            evidence=case.evidence,
            schema_text=case.schema_prompt,
            db_executor=wf.sql_executor,
        )
        collapse = _collapse_stats(raw_a)
        collapse_rows.append({"qid": qid, **collapse})

        paths = run_beam_a_oneshot_rest(
            chat_llm,
            case,
            wf,
            k_a=args.k_a,
            gold_sql=gold_sql,
        )
        paths = llm_judge_rerank(
            chat_llm,
            question=case.question,
            evidence=case.evidence,
            schema_text=case.schema_prompt,
            paths=paths,
        )

        hit1_correct = False
        any_path_correct = False
        if paths:
            top = paths[0]
            hit1_correct = bool(top.exec_match_gold)
            any_path_correct = any(bool(p.exec_match_gold) for p in paths)

        row = {
            "qid": qid,
            "hit1_correct": hit1_correct,
            "any_path_correct": any_path_correct,
            "selected_path_id": paths[0].path_id if paths else "",
            "selected_form_a": paths[0].form_a if paths else "",
            "judge_top1_score": paths[0].judge_score if paths else None,
            "paths": [p.to_dict() for p in paths],
            "a_forms": [
                {
                    "form": c.form_tag,
                    "valid": c.is_valid,
                    "probe_hash": c.probe_hash,
                    "error": c.error,
                }
                for c in raw_a
            ],
            "llm_calls_total": chat_llm.llm_calls_total - calls_before,
            "tokens_total": chat_llm.tokens_total - tokens_before,
            "elapsed_s": time.time() - t0,
        }
        results.append(row)
        print(
            f"[beam-A] {qi}/{len(qids)} qid={qid} hit1={hit1_correct} "
            f"any={any_path_correct} form={row['selected_form_a']} "
            f"judge={row['judge_top1_score']} collapse={collapse['class']}",
            flush=True,
        )

        if out_path:
            payload = {
                "config": {
                    "k_a": args.k_a,
                    "model": getattr(inner_llm, "model", ""),
                    "qids_n": len(qids),
                },
                "results": results,
                "collapse_analysis": collapse_rows,
            }
            out_path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")

    if wf and wf.db_connector:
        try:
            wf.db_connector.disconnect()
        except Exception:
            pass

    qid_results = [
        {
            "question_id": r["qid"],
            "hit1_correct": r.get("hit1_correct"),
            "any_path_correct": r.get("any_path_correct"),
            "gold_available": r.get("hit1_correct") is not None,
        }
        for r in results
        if "error" not in r
    ]
    summary = summarize_qid_ex(qid_results)
    avg_calls = sum(r.get("llm_calls_total", 0) for r in results) / max(len(results), 1)

    collapse_counts = {"all_different": 0, "partial": 0, "all_same": 0, "all_invalid": 0}
    for c in collapse_rows:
        collapse_counts[c.get("class", "all_invalid")] = (
            collapse_counts.get(c.get("class", "all_invalid"), 0) + 1
        )

    payload = {
        "config": {
            "k_a": args.k_a,
            "model": getattr(inner_llm, "model", ""),
            "qids_n": len(qids),
            "llm_backend": args.llm_backend,
        },
        "summary": summary,
        "collapse_analysis": collapse_rows,
        "collapse_counts": collapse_counts,
        "avg_llm_calls_per_qid": round(avg_calls, 1),
        "results": results,
        "elapsed_s": time.time() - t0_all,
    }
    out_path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")

    print("\n== Beam-CTE stage 1 summary ==")
    print(json.dumps(summary, indent=2))
    print(f"A-axis collapse counts: {collapse_counts}")
    print(f"Avg LLM calls/qid: {avg_calls:.1f}")
    print(f"Wrote {out_path}")


if __name__ == "__main__":
    main()
