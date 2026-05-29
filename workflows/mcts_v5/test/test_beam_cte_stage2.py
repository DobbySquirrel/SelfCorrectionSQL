#!/usr/bin/env python3
"""
Beam-CTE stage 2: A + C + D form beam; B/E oneshot v2; judge V2.

Usage (repo root):
  python workflows/mcts_v5/test/test_beam_cte_stage2.py \\
    --qids-file workflows/mcts_v5/test/fixtures/qids_30sample.json \\
    --k-a 3 --k-c 3 --k-d 3 --max-paths 12 \\
    --out workflows/mcts_v5/test/out/beam_cte_stage2.json \\
    2>&1 | tee workflows/mcts_v5/test/out/beam_cte_stage2.log
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

from workflows.mcts_v5.test.beam_cte.beam_runner import (
    dedup_paths_for_judge,
    run_beam_acd_oneshot_rest,
)
from workflows.mcts_v5.test.beam_cte.c_axis_generator import (
    select_c_forms_to_generate,
)
from workflows.mcts_v5.test.beam_cte.d_axis_generator import select_d_forms_to_generate
from workflows.mcts_v5.test.beam_cte.llm_judge import llm_judge_rerank
from workflows.mcts_v5.test.beam_cte.llm_stats import CountingLLM
from workflows.mcts_v5.test.test_taxonomy_prompt_lab import (
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

ZERO_BEAM_LOSS = {"1166", "1505", "248", "371", "408", "529"}


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


def _collapse_stats(candidates: List[Any], *, axis: str) -> Dict[str, Any]:
    hashes = [
        c.probe_hash
        for c in candidates
        if c.probe_hash and c.is_valid and not (c.error or "").startswith("duplicate")
    ]
    unique = len(set(hashes))
    if unique == 0:
        cls = "all_invalid"
    elif unique == 1:
        cls = "all_same"
    elif unique == 2:
        cls = "partial"
    else:
        cls = "all_different"
    return {"axis": axis, "unique_hashes": unique, "class": cls}


def _evaluate_stop_loss(
    results: List[dict],
    *,
    stage1_hit1_pct: float = 43.33,
) -> Dict[str, Any]:
    ok = [r for r in results if "error" not in r]
    n = len(ok)
    hit = sum(1 for r in ok if r.get("hit1_correct"))
    hit_pct = 100.0 * hit / n if n else 0.0
    rescued = sum(
        1 for r in ok if str(r["qid"]) in ZERO_BEAM_LOSS and r.get("hit1_correct")
    )
    new_loss = sum(
        1
        for r in ok
        if str(r["qid"]) not in ZERO_BEAM_LOSS
        and r.get("hit1_correct") is False
        and r.get("_zero_hit1")
    )
    avg_calls = sum(r.get("llm_calls_total", 0) for r in ok) / max(n, 1)

    verdict = "FAIL"
    if hit_pct < stage1_hit1_pct:
        verdict = "REGRESS"
    elif avg_calls > 60:
        verdict = "超预算"
    elif hit_pct >= 50 and rescued >= 4:
        verdict = "PASS"
    elif hit_pct >= 47:
        verdict = "MARGINAL"

    return {
        "verdict": verdict,
        "hit1_pct": round(hit_pct, 2),
        "rescued_zero_loss": rescued,
        "avg_llm_calls": round(avg_calls, 1),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Beam-CTE stage 2 (A+C+D beam)")
    parser.add_argument(
        "--qids-file",
        type=str,
        default="workflows/mcts_v5/test/fixtures/qids_30sample.json",
    )
    parser.add_argument("--data-file", type=str, default=str(DEFAULT_ARCWISE_DATA))
    parser.add_argument("--gold-file", type=str, default="")
    parser.add_argument("--k-a", type=int, default=3)
    parser.add_argument("--k-c", type=int, default=3)
    parser.add_argument("--k-d", type=int, default=3)
    parser.add_argument("--max-paths", type=int, default=12)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument(
        "--out",
        type=str,
        default="workflows/mcts_v5/test/out/beam_cte_stage2.json",
    )
    parser.add_argument("--llm-backend", choices=("local", "yizhan"), default="local")
    parser.add_argument("--llm-preset", type=str, default="")
    parser.add_argument("--k-samples", type=int, default=5)
    parser.add_argument("--k-regen", type=int, default=3)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--judge-mode", choices=("v1", "v2"), default="v2")
    parser.add_argument(
        "--zero-json",
        type=str,
        default="workflows/mcts_v5/test/out/compare_zero_qids.json",
        help="For stop-loss new-regression count",
    )
    args = parser.parse_args()

    qid_path = Path(args.qids_file)
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    qids = _load_qids(qid_path) if qid_path.is_file() else []
    if args.limit > 0:
        qids = qids[: args.limit]

    if args.dry_run:
        print(
            f"Dry run: {len(qids)} qids, k_a={args.k_a} k_c={args.k_c} "
            f"k_d={args.k_d} max_paths={args.max_paths}"
        )
        for qid in ("408", "1505", "371", "1166", "529", "738"):
            case = case_from_qid(qid, Path(args.data_file), 0)
            cf = select_c_forms_to_generate(case.question, case.evidence or "", a_form_tag="INNER")
            df = select_d_forms_to_generate(case.question, case.evidence or "")
            print(f"  qid={qid} C={cf} D={df}")
        return

    zero_hit: Dict[str, bool] = {}
    zp = Path(args.zero_json)
    if zp.is_file():
        zd = json.loads(zp.read_text(encoding="utf-8"))
        for row in zd.get("results") or []:
            qid = str(row.get("question_id", ""))
            zs = (row.get("methods") or {}).get("zero_shot") or {}
            zero_hit[qid] = bool(zs.get("hit1_correct"))

    inner_llm = create_llm(args.llm_backend, preset=args.llm_preset or None)
    chat_llm = CountingLLM(inner_llm)

    gold_path = (
        Path(args.gold_file) if args.gold_file else default_bird_dev_gold_path(_REPO)
    )
    gold_map = load_gold_sql_map(gold_path) if gold_path.is_file() else {}

    data_file = Path(args.data_file)
    wf: Optional[TaxonomyMCTSWorkflow] = None
    wf_db = ""

    results: List[dict] = []
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
        t0 = time.time()

        sample = {"question_id": qid, "db": case.db}
        gold_sql = resolve_gold_sql(sample, gold_map)

        paths_all, meta = run_beam_acd_oneshot_rest(
            chat_llm,
            case,
            wf,
            k_a=args.k_a,
            k_c=args.k_c,
            k_d=args.k_d,
            max_paths=args.max_paths,
            gold_sql=gold_sql,
        )

        any_path_pre_dedup = any(bool(p.exec_match_gold) for p in paths_all)
        paths_for_judge = dedup_paths_for_judge(paths_all)
        paths_judged = llm_judge_rerank(
            chat_llm,
            question=case.question,
            evidence=case.evidence,
            schema_text=case.schema_prompt,
            paths=paths_for_judge,
            mode=args.judge_mode,
        )

        hit1_correct = False
        any_path_correct = False
        top = paths_judged[0] if paths_judged else None
        if top:
            hit1_correct = bool(top.exec_match_gold)
            any_path_correct = any(bool(p.exec_match_gold) for p in paths_judged)

        row = {
            "qid": qid,
            "hit1_correct": hit1_correct,
            "any_path_correct": any_path_correct,
            "any_path_pre_dedup": any_path_pre_dedup,
            "n_paths_pre_dedup": len(paths_all),
            "n_paths_for_judge": len(paths_for_judge),
            "selected_path_id": top.path_id if top else "",
            "selected_form_a": top.form_a if top else "",
            "selected_form_c": top.form_c if top else "",
            "selected_form_d": top.form_d if top else "",
            "judge_top1_score": top.judge_score if top else None,
            "triggered_c_forms": meta.get("triggered_c_forms"),
            "triggered_d_forms": meta.get("triggered_d_forms"),
            "paths": [p.to_dict() for p in paths_all],
            "paths_for_judge": [p.to_dict() for p in paths_judged],
            "llm_calls_total": chat_llm.llm_calls_total - calls_before,
            "elapsed_s": time.time() - t0,
            "_zero_hit1": zero_hit.get(str(qid)),
        }
        results.append(row)
        print(
            f"[beam-ACD] {qi}/{len(qids)} qid={qid} hit1={hit1_correct} "
            f"any={any_path_correct} pre={any_path_pre_dedup} "
            f"A={row['selected_form_a']} C={row['selected_form_c']} D={row['selected_form_d']} "
            f"judge={row['judge_top1_score']} paths={len(paths_all)} "
            f"Ctrig={meta.get('triggered_c_forms')} Dtrig={meta.get('triggered_d_forms')}",
            flush=True,
        )

        if out_path:
            payload = {
                "config": {
                    "stage": 2,
                    "k_a": args.k_a,
                    "k_c": args.k_c,
                    "k_d": args.k_d,
                    "max_paths": args.max_paths,
                    "judge_mode": args.judge_mode,
                },
                "results": results,
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
            "any_path_pre_dedup": r.get("any_path_pre_dedup"),
            "gold_available": True,
        }
        for r in results
        if "error" not in r
    ]
    summary = summarize_qid_ex(qid_results)
    stop = _evaluate_stop_loss(results)
    avg_calls = sum(r.get("llm_calls_total", 0) for r in results if "error" not in r) / max(
        len([r for r in results if "error" not in r]), 1
    )

    payload = {
        "config": {
            "stage": 2,
            "k_a": args.k_a,
            "k_c": args.k_c,
            "k_d": args.k_d,
            "max_paths": args.max_paths,
            "judge_mode": args.judge_mode,
            "model": getattr(inner_llm, "model", ""),
            "qids_n": len(qids),
        },
        "summary": summary,
        "stop_loss": stop,
        "avg_llm_calls_per_qid": round(avg_calls, 1),
        "results": results,
        "elapsed_s": time.time() - t0_all,
    }
    out_path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")

    print("\n== Beam-CTE stage 2 summary ==")
    print(json.dumps(summary, indent=2))
    print(f"Stop-loss: {stop}")
    print(f"Avg LLM calls/qid: {avg_calls:.1f}")
    print(f"Wrote {out_path}")


if __name__ == "__main__":
    main()
