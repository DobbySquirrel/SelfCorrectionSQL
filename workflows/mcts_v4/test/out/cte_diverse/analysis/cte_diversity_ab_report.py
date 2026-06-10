#!/usr/bin/env python3
"""Report for CT1-v2 / CT2 CTE diversity A/B harness."""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from statistics import mean

ROOT = Path(__file__).resolve().parents[6]
OUT_DIR = ROOT / "workflows/mcts_v4/test/out/cte_diverse/ct1v2_ct2"
REPORT = ROOT / "workflows/mcts_v4/test/out/cte_diverse/analysis/cte_diversity_ab_report.md"


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def main():
    json_path = Path(sys.argv[1]) if len(sys.argv) > 1 else OUT_DIR / "cte_diversity_ab_merged.json"
    if not json_path.is_file():
        print(f"missing {json_path}", file=sys.stderr)
        sys.exit(1)
    data = _load(json_path)
    lines = [
        "# CT1-v2 / CT2 — CTE Diversity A/B",
        "",
        f"Generated: {datetime.now().isoformat(timespec='seconds')}",
        f"JSON: `{json_path}`",
        "",
    ]

    has_ct1 = any("ct1v2_delta" in rec for rec in data.values())
    has_ct2 = any("ct2_delta" in rec for rec in data.values())

    if has_ct1:
        lines.extend(
            [
                "## CT1-v2: diverse instruction vs temperature (5 calls vs 1 call, ~5 candidates)",
                "",
                "| qid | A struct | B struct | Δ | A result | B result | Δ | B wins struct |",
                "|---|---:|---:|---:|---:|---:|---:|:---:|",
            ]
        )
        wins_s = wins_r = 0
        deltas_s = []
        for qid, rec in sorted(data.items(), key=lambda x: int(x[0])):
            d = rec.get("ct1v2_delta") or {}
            ma, mb = rec["modes"]["A"], rec["modes"]["B"]
            lines.append(
                f"| {qid} | {ma['n_unique_structure']} | {mb['n_unique_structure']} | "
                f"{d.get('structure', 0):+d} | {ma['n_unique_result']} | {mb['n_unique_result']} | "
                f"{d.get('result', 0):+d} | {'✓' if d.get('B_wins_structure') else '✗'} |"
            )
            if d.get("B_wins_structure"):
                wins_s += 1
            if d.get("B_wins_result"):
                wins_r += 1
            deltas_s.append(d.get("structure", 0))
        n = len(data)
        pass_struct = wins_s >= (n + 1) // 2
        lines.extend(
            [
                "",
                f"- B wins structure on **{wins_s}/{n}** qids",
                f"- Mean Δ unique structure (B−A): **{mean(deltas_s):+.2f}**",
                f"- **CT1-v2 gate (structure): {'PASS' if pass_struct else 'FAIL'}** "
                f"(need B > A on majority qids)",
                "",
            ]
        )

    if has_ct2:
        lines.extend(
            [
                "## CT2: diverse×3temp vs standard×12temp (deduped budget ~12)",
                "",
                "| qid | A' struct | C struct | Δ | A' result | C result | Δ | C wins |",
                "|---|---:|---:|---:|---:|---:|---:|:---:|",
            ]
        )
        wins = 0
        for qid, rec in sorted(data.items(), key=lambda x: int(x[0])):
            d = rec.get("ct2_delta") or {}
            ma, mc = rec["modes"]["A_prime"], rec["modes"]["C"]
            lines.append(
                f"| {qid} | {ma['n_unique_structure']} | {mc['n_unique_structure']} | "
                f"{d.get('structure', 0):+d} | {ma['n_unique_result']} | {mc['n_unique_result']} | "
                f"{d.get('result', 0):+d} | {'✓' if d.get('C_wins_structure') else '✗'} |"
            )
            if d.get("C_wins_structure"):
                wins += 1
        n = len(data)
        lines.extend(
            [
                "",
                f"- C wins structure on **{wins}/{n}** qids",
                f"- **CT2 gate (structure): {'PASS' if wins >= (n + 1) // 2 else 'FAIL'}**",
                "",
            ]
        )

    lines.append("## Per-qid candidate audit")
    lines.append("")
    for qid, rec in sorted(data.items(), key=lambda x: int(x[0])):
        lines.append(f"### qid={qid} — {rec.get('sub_question', '')[:120]}")
        for mode_key in ("A", "B", "C", "A_prime"):
            if mode_key not in rec.get("modes", {}):
                continue
            m = rec["modes"][mode_key]
            lines.append(
                f"- **{mode_key}** ({m.get('label')}): "
                f"candidates={m.get('n_candidates')} unique_struct={m.get('n_unique_structure')} "
                f"unique_result={m.get('n_unique_result')} calls={m.get('n_llm_calls')}"
            )
            for i, det in enumerate(m.get("details") or [], 1):
                rat = ""
                if mode_key == "B" and m.get("audit"):
                    for c in m["audit"].get("candidates") or []:
                        if c.get("cte") == det.get("cte"):
                            rat = c.get("rationale", "")[:100]
                            break
                lines.append(f"  - #{i} struct={det.get('structure_sig', '')[:8]}… "
                               f"result={str(det.get('result_sig', ''))[:8]}… "
                               f"valid={det.get('exec_valid')} {rat}")
        lines.append("")

    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines[-20:]))
    print(f"\nWrote {REPORT}")


if __name__ == "__main__":
    main()
