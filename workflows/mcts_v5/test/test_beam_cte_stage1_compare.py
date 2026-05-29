#!/usr/bin/env python3
"""Compare zero-shot / oneshot v2 / beam-A stage1 on common qids."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

_REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO))

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


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _hit_any_from_zero(data: dict) -> Dict[str, dict]:
    out: Dict[str, dict] = {}
    for row in data.get("results") or []:
        qid = str(row.get("question_id", ""))
        zs = (row.get("methods") or {}).get("zero_shot") or {}
        out[qid] = {
            "hit1": zs.get("hit1_correct"),
            "any_path": zs.get("any_path_correct"),
        }
    return out


def _hit_any_from_lab(data: dict) -> Dict[str, dict]:
    out: Dict[str, dict] = {}
    for row in data.get("qid_results") or []:
        qid = str(row.get("question_id", ""))
        out[qid] = {
            "hit1": row.get("hit1_correct"),
            "any_path": row.get("any_path_correct"),
        }
    return out


def _hit_any_from_beam(data: dict) -> Dict[str, dict]:
    out: Dict[str, dict] = {}
    for row in data.get("results") or []:
        qid = str(row.get("qid", ""))
        out[qid] = {
            "hit1": row.get("hit1_correct"),
            "any_path": row.get("any_path_correct"),
            "form_a": row.get("selected_form_a"),
            "judge": row.get("judge_top1_score"),
        }
    return out


def _pct(num: int, den: int) -> float:
    return round(100.0 * num / den, 2) if den else 0.0


def _summarize(name: str, m: Dict[str, dict], qids: List[str]) -> dict:
    hit = sum(1 for q in qids if m.get(q, {}).get("hit1") is True)
    anyp = sum(1 for q in qids if m.get(q, {}).get("any_path") is True)
    n = len(qids)
    return {
        "method": name,
        "n": n,
        "hit1_correct": hit,
        "hit1_accuracy_pct": _pct(hit, n),
        "any_path_correct": anyp,
        "any_path_accuracy_pct": _pct(anyp, n),
    }


def _stop_loss(beam: dict, zero: dict, oneshot: dict) -> str:
    b_any = beam.get("any_path_accuracy_pct", 0)
    b_hit = beam.get("hit1_accuracy_pct", 0)
    z_any = zero.get("any_path_accuracy_pct", 0)
    z_hit = zero.get("hit1_accuracy_pct", 0)
    o_hit = oneshot.get("hit1_accuracy_pct", 0)
    if b_hit < z_hit - 5.0:
        return "严重退化"
    if b_any >= z_any + 5.0 and b_hit >= o_hit + 3.0:
        return "PASS"
    if b_any >= z_any + 5.0 and b_hit < o_hit + 3.0:
        return "JUDGE 不够强"
    return "FAIL"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--zero",
        default="workflows/mcts_v5/test/out/compare_zero_qids.json",
    )
    parser.add_argument(
        "--oneshot",
        default="workflows/mcts_v5/test/out/prompt_lab_oneshot_v2.json",
    )
    parser.add_argument(
        "--beam",
        default="workflows/mcts_v5/test/out/beam_cte_stage1.json",
    )
    parser.add_argument(
        "--out-md",
        default="workflows/mcts_v5/test/out/beam_cte_stage1_report.md",
    )
    args = parser.parse_args()

    zero_data = _load_json(Path(args.zero))
    oneshot_data = _load_json(Path(args.oneshot))
    beam_path = Path(args.beam)
    beam_data = _load_json(beam_path) if beam_path.is_file() else {"results": []}

    zmap = _hit_any_from_zero(zero_data)
    omap = _hit_any_from_lab(oneshot_data)
    bmap = _hit_any_from_beam(beam_data)

    common = sorted(set(zmap) & set(omap) & set(bmap))
    z_s = _summarize("zero-shot", zmap, common)
    o_s = _summarize("oneshot v2", omap, common)
    b_s = _summarize("beam-A k=3", bmap, common)

    avg_calls = 0.0
    if beam_data.get("results"):
        avg_calls = float(beam_data.get("avg_llm_calls_per_qid") or 0)
        if not avg_calls:
            calls = [r.get("llm_calls_total", 0) for r in beam_data["results"]]
            avg_calls = sum(calls) / max(len(calls), 1)

    zero_reg = sorted(q for q in common if zmap[q].get("hit1") and not bmap[q].get("hit1"))
    oneshot_reg = sorted(
        q for q in common if omap[q].get("hit1") and not bmap[q].get("hit1")
    )
    beam_gain = sorted(q for q in common if bmap[q].get("hit1") and not zmap[q].get("hit1"))

    verdict = _stop_loss(b_s, z_s, o_s)

    lines = [
        "# Beam-CTE Stage 1 Report",
        "",
        f"Common qids: **{len(common)}**",
        "",
        "| Method | hit1 | any_path | LLM calls/题 (avg) |",
        "|--------|------|----------|---------------------|",
        f"| zero-shot | {z_s['hit1_accuracy_pct']}% | {z_s['any_path_accuracy_pct']}% | ~5 |",
        f"| oneshot v2 | {o_s['hit1_accuracy_pct']}% | {o_s['any_path_accuracy_pct']}% | ~25 |",
        f"| beam-A k=3 | {b_s['hit1_accuracy_pct']}% | {b_s['any_path_accuracy_pct']}% | {avg_calls:.1f} |",
        "",
        f"**Stop-loss verdict: {verdict}**",
        "",
        "## Cross lists",
        "",
        f"- zero ✓, beam ✗: `{zero_reg}`",
        f"- oneshot v2 ✓, beam ✗: `{oneshot_reg}`",
        f"- beam ✓, zero ✗: `{beam_gain}`",
        "",
        "## Required qids (10)",
        "",
        "| qid | zero hit1 | oneshot hit1 | beam hit1 | beam any | form_a | judge |",
        "|-----|-----------|--------------|-----------|----------|--------|-------|",
    ]

    for qid in sorted(REQUIRED_QIDS):
        if qid not in common and qid not in bmap:
            lines.append(f"| {qid} | - | - | - | - | - | - |")
            continue
        lines.append(
            f"| {qid} | {zmap.get(qid, {}).get('hit1')} | {omap.get(qid, {}).get('hit1')} | "
            f"{bmap.get(qid, {}).get('hit1')} | {bmap.get(qid, {}).get('any_path')} | "
            f"{bmap.get(qid, {}).get('form_a', '')} | {bmap.get(qid, {}).get('judge', '')} |"
        )

    if beam_data.get("collapse_counts"):
        lines.extend(
            [
                "",
                "## A-axis collapse (30-q run)",
                "",
                f"```json\n{json.dumps(beam_data['collapse_counts'], indent=2)}\n```",
            ]
        )

    md = "\n".join(lines)
    out_md = Path(args.out_md)
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_md.write_text(md, encoding="utf-8")

    print(md)
    print(f"\nWrote {out_md}")


if __name__ == "__main__":
    main()
