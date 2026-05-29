#!/usr/bin/env python3
"""Compare zero-shot / oneshot v2 / beam-A / beam-ACD on common qids."""

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
            "any_path_pre_dedup": row.get("any_path_pre_dedup"),
            "form_a": row.get("selected_form_a"),
            "form_c": row.get("selected_form_c"),
            "form_d": row.get("selected_form_d"),
            "judge": row.get("judge_top1_score"),
        }
    return out


def _pct(num: int, den: int) -> float:
    return round(100.0 * num / den, 2) if den else 0.0


def _summarize(name: str, m: Dict[str, dict], qids: List[str], *, any_key: str = "any_path") -> dict:
    hit = sum(1 for q in qids if m.get(q, {}).get("hit1") is True)
    anyp = sum(1 for q in qids if m.get(q, {}).get(any_key) is True)
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
        help="beam-A stage1 JSON",
    )
    parser.add_argument(
        "--beam-stage2",
        default="",
        help="beam-ACD stage2 JSON (optional)",
    )
    parser.add_argument(
        "--label",
        default="stage1",
        help="Report title label",
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
    s2_path = Path(args.beam_stage2) if args.beam_stage2 else None
    s2_data = (
        _load_json(s2_path) if s2_path and s2_path.is_file() else {"results": []}
    )

    zmap = _hit_any_from_zero(zero_data)
    omap = _hit_any_from_lab(oneshot_data)
    bmap = _hit_any_from_beam(beam_data)
    s2map = _hit_any_from_beam(s2_data) if s2_data.get("results") else {}

    common = sorted(set(zmap) & set(omap))
    if bmap:
        common = sorted(set(common) & set(bmap))
    if s2map:
        common = sorted(set(common) & set(s2map))

    z_s = _summarize("zero-shot", zmap, common)
    o_s = _summarize("oneshot v2", omap, common)
    b_s = _summarize("beam-A k=3", bmap, common) if bmap else None
    b_pre = (
        _summarize("beam-A pre-dedup", bmap, common, any_key="any_path_pre_dedup")
        if bmap
        else None
    )
    s2_s = _summarize("beam-ACD", s2map, common) if s2map else None
    s2_pre = (
        _summarize("beam-ACD pre-dedup", s2map, common, any_key="any_path_pre_dedup")
        if s2map
        else None
    )

    def _avg_calls(data: dict) -> float:
        if data.get("avg_llm_calls_per_qid"):
            return float(data["avg_llm_calls_per_qid"])
        calls = [r.get("llm_calls_total", 0) for r in data.get("results") or []]
        return sum(calls) / max(len(calls), 1) if calls else 0.0

    avg_calls = _avg_calls(beam_data) if bmap else 0.0
    avg_s2 = _avg_calls(s2_data) if s2map else 0.0

    bmap_cmp = s2map if s2map else bmap
    zero_reg = sorted(
        q for q in common if zmap[q].get("hit1") and not bmap_cmp.get(q, {}).get("hit1")
    )
    oneshot_reg = sorted(
        q for q in common if omap[q].get("hit1") and not bmap_cmp.get(q, {}).get("hit1")
    )
    beam_gain = sorted(
        q for q in common if bmap_cmp.get(q, {}).get("hit1") and not zmap[q].get("hit1")
    )

    verdict = _stop_loss(s2_s or b_s or {}, z_s, o_s) if (s2_s or b_s) else "N/A"
    if s2_data.get("stop_loss"):
        verdict = s2_data["stop_loss"].get("verdict", verdict)

    title = f"Beam-CTE Report ({args.label})"
    lines = [
        f"# {title}",
        "",
        f"Common qids: **{len(common)}**",
        "",
        "| Method | hit1 | any_path | any_pre_dedup | LLM calls/题 (avg) |",
        "|--------|------|----------|---------------|---------------------|",
        f"| zero-shot | {z_s['hit1_accuracy_pct']}% | {z_s['any_path_accuracy_pct']}% | - | ~5 |",
        f"| oneshot v2 | {o_s['hit1_accuracy_pct']}% | {o_s['any_path_accuracy_pct']}% | - | ~25 |",
    ]
    if b_s:
        lines.append(
            f"| beam-A k=3 | {b_s['hit1_accuracy_pct']}% | {b_s['any_path_accuracy_pct']}% | "
            f"{b_pre['any_path_accuracy_pct'] if b_pre else '-'}% | {avg_calls:.1f} |"
        )
    if s2_s:
        lines.append(
            f"| beam-ACD | {s2_s['hit1_accuracy_pct']}% | {s2_s['any_path_accuracy_pct']}% | "
            f"{s2_pre['any_path_accuracy_pct'] if s2_pre else '-'}% | {avg_s2:.1f} |"
        )
    lines.extend(
        [
            "",
            f"**Stop-loss verdict: {verdict}**",
            "",
            "## Cross lists (vs primary beam column)",
            "",
            f"- zero ✓, beam ✗: `{zero_reg}`",
            f"- oneshot v2 ✓, beam ✗: `{oneshot_reg}`",
            f"- beam ✓, zero ✗: `{beam_gain}`",
            "",
            "## Required qids (10)",
            "",
        ]
    )
    if s2map:
        lines.append(
            "| qid | zero hit1 | oneshot hit1 | beam-A hit1 | beam-ACD hit1 | "
            "form_a | form_c | form_d | judge |"
        )
        lines.append(
            "|-----|-----------|--------------|-------------|---------------|"
            "--------|--------|--------|-------|"
        )
    else:
        lines.append(
            "| qid | zero hit1 | oneshot hit1 | beam hit1 | beam any | form_a | judge |"
        )
        lines.append(
            "|-----|-----------|--------------|-----------|----------|--------|-------|"
        )

    for qid in sorted(REQUIRED_QIDS):
        if s2map:
            lines.append(
                f"| {qid} | {zmap.get(qid, {}).get('hit1')} | {omap.get(qid, {}).get('hit1')} | "
                f"{bmap.get(qid, {}).get('hit1')} | {s2map.get(qid, {}).get('hit1')} | "
                f"{s2map.get(qid, {}).get('form_a', '')} | {s2map.get(qid, {}).get('form_c', '')} | "
                f"{s2map.get(qid, {}).get('form_d', '')} | {s2map.get(qid, {}).get('judge', '')} |"
            )
        elif qid not in common and qid not in bmap:
            lines.append(f"| {qid} | - | - | - | - | - | - |")
        else:
            lines.append(
                f"| {qid} | {zmap.get(qid, {}).get('hit1')} | {omap.get(qid, {}).get('hit1')} | "
                f"{bmap.get(qid, {}).get('hit1')} | {bmap.get(qid, {}).get('any_path')} | "
                f"{bmap.get(qid, {}).get('form_a', '')} | {bmap.get(qid, {}).get('judge', '')} |"
            )

    loss6 = ["1166", "1505", "248", "371", "408", "529"]
    if s2map:
        lines.extend(["", "## zero✓/beam✗ 六题 (stage2)", ""])
        lines.append("| qid | zero | beam-ACD hit1 | A | C | D |")
        lines.append("|-----|------|---------------|---|---|---|")
        for qid in loss6:
            lines.append(
                f"| {qid} | {zmap.get(qid, {}).get('hit1')} | {s2map.get(qid, {}).get('hit1')} | "
                f"{s2map.get(qid, {}).get('form_a', '')} | {s2map.get(qid, {}).get('form_c', '')} | "
                f"{s2map.get(qid, {}).get('form_d', '')} |"
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
