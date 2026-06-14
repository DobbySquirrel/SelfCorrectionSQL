#!/usr/bin/env python3
"""Report for ablation5 gap30 arms + optional P0 offline JSON."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[7]
OUT = ROOT / "workflows/mcts_v4/test/out/cte_diverse"
PLAN = Path(__file__).resolve().parent
MANIFEST = PLAN / "qids_alpha_min2_recall_gap30_manifest.json"

ARMS = {
    "E1 baseline": OUT / "v4_colbind_v2_dual03_min2sq_abl5_e1_baseline_gap30_r12.json",
    "E2 dedup": OUT / "v4_colbind_v2_dual03_min2sq_abl5_e2_dedup_gap30_r12.json",
    "E3 reversed": OUT / "v4_colbind_v2_dual03_min2sq_abl5_e3_reversed_gap30_r12.json",
    "E4 fk_pk": OUT / "v4_colbind_v2_dual03_min2sq_abl5_e4_fkpk_gap30_r12.json",
    "E5 reversed+fk_pk": OUT / "v4_colbind_v2_dual03_min2sq_abl5_e5_reversed_fkpk_gap30_r12.json",
    "E6 reversed+bootstrap": OUT / "v4_colbind_v2_dual03_min2sq_abl5_e6_reversed_bootstrap_gap30_r12.json",
    "E7 bootstrap+fk_pk": OUT / "v4_colbind_v2_dual03_min2sq_abl5_e7_bootstrap_fkpk_gap30_r12.json",
    "E8 v2 bootstrap+fk_link": OUT / "v4_colbind_v2_dual03_min2sq_abl5_e8_v2_bootstrap_fkpk_gap30_r12.json",
    "E9 bootstrap once": OUT / "v4_colbind_v2_dual03_min2sq_abl5_e9_bootstrap_once_gap30_r12.json",
}


def load_merged(path: Path) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text())


def metrics(data: dict, cohort: list[str]) -> dict:
    if not data:
        return {}
    qs = [q for q in cohort if q in data]
    recall = acc = 0
    llm_calls = 0
    reversed_exp = 0
    bootstrap_exp = 0
    norm_dedup = 0
    expands = 0
    for q in qs:
        rec = data[q]
        attrs = rec.get("all_sqls_with_attributes") or []
        if any(a.get("is_correct") for a in attrs):
            recall += 1
        if (rec.get("stats") or {}).get("gold_match"):
            acc += 1
        for tr in rec.get("decompose_expand_traces") or []:
            expands += 1
            llm_calls += int(tr.get("n_llm_calls") or 0)
            if tr.get("reversed_schema_linking"):
                reversed_exp += 1
            if tr.get("reversed_bootstrap_direct_sql"):
                bootstrap_exp += 1
            norm_dedup += int(tr.get("n_norm_deduped_dropped") or 0)
    avg_llm = llm_calls / max(1, expands)
    return {
        "n": len(qs),
        "recall": recall,
        "acc": acc,
        "llm_calls_total": llm_calls,
        "expands": expands,
        "avg_llm_per_expand": round(avg_llm, 2),
        "reversed_expands": reversed_exp,
        "bootstrap_expands": bootstrap_exp,
        "norm_dedup_dropped": norm_dedup,
    }


def main() -> None:
    cohort = []
    if MANIFEST.exists():
        cohort = [str(q) for q in json.loads(MANIFEST.read_text()).get("qids") or []]

    lines = ["# Ablation5 — alpha/min2 recall-gap 30q", ""]
    if cohort:
        lines.append(f"Cohort: {len(cohort)} qids from `{MANIFEST.name}`")
        lines.append("")

    p0 = PLAN / "p0_exec_time_tiebreak_dual03.json"
    if p0.exists():
        j = json.loads(p0.read_text())
        lines.extend(
            [
                "## P0 offline (dual03 global 498, exec-time tiebreak)",
                "",
                f"- Prod Acc: {j.get('prod_acc')}/{j.get('n')}",
                f"- R4 exec-time Acc: {j.get('r4_exec_time_acc')}/{j.get('n')}",
                f"- **Δ vs prod: {j.get('delta_vs_prod'):+d}**",
                f"- improved/hurt: {len(j.get('improved') or [])}/{len(j.get('hurt') or [])}",
                f"- tie-rescued (R4-row still wrong): {len(j.get('tie_rescued') or [])}",
                "",
            ]
        )

    lines.extend(["## Live arms (30q cohort)", "", "| Arm | Recall | Acc | avg LLM/expand | reversed_exp | bootstrap_exp | norm_dedup |", "|---|---:|---:|---:|---:|---:|---:|"])
    baseline_llm = None
    for name, path in ARMS.items():
        m = metrics(load_merged(path), cohort)
        if not m:
            lines.append(f"| {name} | — | — | — | — | — | — |")
            continue
        if name.startswith("E1"):
            baseline_llm = m["avg_llm_per_expand"]
        lines.append(
            f"| {name} | {m['recall']}/{m['n']} | {m['acc']}/{m['n']} | {m['avg_llm_per_expand']} | {m['reversed_expands']} | {m['bootstrap_expands']} | {m['norm_dedup_dropped']} |"
        )

    if baseline_llm:
        lines.extend(["", "### vs E1 baseline avg LLM/expand", ""])
        e1 = metrics(load_merged(ARMS["E1 baseline"]), cohort)
        for name, path in ARMS.items():
            if name.startswith("E1"):
                continue
            m = metrics(load_merged(path), cohort)
            if not m or not e1:
                continue
            pct = 100.0 * (m["avg_llm_per_expand"] - e1["avg_llm_per_expand"]) / max(0.01, e1["avg_llm_per_expand"])
            dr = m["recall"] - e1["recall"]
            da = m["acc"] - e1["acc"]
            lines.append(f"- **{name}**: LLM {pct:+.1f}%, ΔRecall {dr:+d}, ΔAcc {da:+d}")

    out_md = PLAN / "ablation5_gap30_report.md"
    out_md.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(out_md.read_text())


if __name__ == "__main__":
    main()
