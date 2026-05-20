#!/usr/bin/env python3
"""Run NL vs DSL question comparison on five predefined cases."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from experiment.pipeline.executor import execute_sql
from experiment.pipeline.items import InteractionItem
from experiment.pipeline.llm_client import LLMClient, load_config
from experiment.pipeline.selectors import World
from question_generation.axis_aggregation import aggregate_axes, build_pairwise_diffs
from question_generation.llm_rendering import fallback_render
from question_generation.pool_builder import (
    append_nota_option,
    build_atomic_pool,
    worlds_from_items,
)

PHASE_B = "experiment/runs/phaseB_bird116_20260517_220736.jsonl"

DEFAULT_CASES = [
    {"case_id": "BIRD_qid_1031", "qid": "1031"},
    {"case_id": "BIRD_qid_1094", "qid": "1094"},
    {"case_id": "BIRD_qid_1387", "qid": "1387"},
    {"case_id": "BIRD_qid_1389", "qid": "1389"},
    {"case_id": "BIRD_qid_145", "qid": "145"},
]


def load_jsonl(path: Path) -> list[dict]:
    rows = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def build_worlds_from_row(row: dict, db_path: str, timeout: float) -> list[World]:
    sqls = row.get("sampled_sqls") or []
    counts: dict[str, int] = {}
    rep: dict[str, str] = {}
    for sql in sqls:
        if not sql or not str(sql).strip():
            continue
        r = execute_sql(db_path, str(sql), timeout_s=timeout)
        if r.ok and r.result_hash:
            h = r.result_hash
            counts[h] = counts.get(h, 0) + 1
            rep.setdefault(h, str(sql))
    return [
        World(hash=h, sample_count=counts[h], representative_sql=rep[h])
        for h in counts
    ]


def run_one_case(
    spec: dict,
    rows_by_qid: dict[str, dict],
    db_path_by_qid: dict[str, str],
    llm: LLMClient | None,
    timeout: float,
    out_dir: Path,
) -> None:
    qid = spec["qid"]
    row = rows_by_qid.get(qid)
    if not row:
        print(f"skip {spec['case_id']}: qid {qid} not in input")
        return
    db_path = db_path_by_qid.get(qid)
    if not db_path or not Path(db_path).exists():
        print(f"skip {spec['case_id']}: no db_path")
        return

    worlds = build_worlds_from_row(row, db_path, timeout)
    if len(worlds) < 2:
        print(f"skip {spec['case_id']}: |W|={len(worlds)} < 2")
        return

    items = [
        InteractionItem(
            key=w.hash,
            weight=w.sample_count,
            exec_hash=w.hash,
            representative_sql=w.representative_sql,
        )
        for w in worlds
    ]
    qg_worlds = worlds_from_items(items)
    diffs = build_pairwise_diffs(qg_worlds, db_path=db_path)
    axes = aggregate_axes(qg_worlds, diffs, db_path=db_path)

    question = row.get("question", "")
    rendered_list = build_atomic_pool(
        qg_worlds,
        diffs,
        question,
        llm,
        use_nl_rendering=llm is not None,
        db_path=db_path,
        append_nota=True,
    )
    rendered_by_axis = {r.axis_id: r for r in rendered_list}

    case_out = {
        "case_id": spec["case_id"],
        "original_question": question,
        "worlds": [
            {
                "world_id": w.hash,
                "representative_sql": w.representative_sql,
                "exec_hash": w.hash,
            }
            for w in worlds
        ],
        "axes": [
            {
                "axis_id": ax.axis_id,
                "unit_type": ax.unit_type,
                "partition": ax.partition,
            }
            for ax in axes
        ],
        "rendered_questions": [],
        "manual_eval": {
            "fidelity": None,
            "readability_old": None,
            "readability_new": None,
            "note": "to be filled by human",
        },
    }

    for ax in axes:
        old_fb = fallback_render(ax)
        append_nota_option(old_fb)
        new_r = rendered_by_axis.get(ax.axis_id, old_fb)
        case_out["rendered_questions"].append({
            "axis_id": ax.axis_id,
            "old_style": {
                "semantic_focus": old_fb.semantic_focus,
                "options": [o["nl_text"] for o in old_fb.options],
            },
            "new_style": {
                "semantic_focus": new_r.semantic_focus,
                "options": [o["nl_text"] for o in new_r.options],
                "fidelity_passed": new_r.fidelity_passed,
            },
        })

    out_path = out_dir / f"{spec['case_id']}.json"
    out_path.write_text(json.dumps(case_out, indent=2, ensure_ascii=False))
    print(f"wrote {out_path}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--llm-preset", default="yi_zhan_gpt-4o")
    ap.add_argument("--no-llm", action="store_true",
                    help="skip LLM; new_style uses DSL fallback only")
    ap.add_argument("--timeout", type=float, default=10.0)
    ap.add_argument("--input", default=PHASE_B)
    ap.add_argument("--bird-gold", default="experiment/runs/bird116_gold_hash.jsonl")
    ap.add_argument("--out-dir", default="case_studies/outputs")
    args = ap.parse_args()

    cfg = load_config()
    bird_root = Path(cfg["data"]["bird_db_root"])
    out_dir = ROOT / args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    gold_lookup = {str(r["qid"]): r for r in load_jsonl(ROOT / args.bird_gold)}
    db_path_by_qid: dict[str, str] = {}
    for qid, g in gold_lookup.items():
        db_id = g.get("db_id", "")
        if db_id:
            db_path_by_qid[qid] = str(bird_root / db_id / f"{db_id}.sqlite")

    rows_by_qid = {
        str(r["qid"]): r for r in load_jsonl(ROOT / args.input)
    }
    llm = None if args.no_llm else LLMClient(preset=args.llm_preset)

    for spec in DEFAULT_CASES:
        run_one_case(
            spec, rows_by_qid, db_path_by_qid, llm, args.timeout, out_dir,
        )


if __name__ == "__main__":
    main()
