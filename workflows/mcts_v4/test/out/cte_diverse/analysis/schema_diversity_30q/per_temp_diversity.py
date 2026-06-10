#!/usr/bin/env python3
"""Task B report: per-temp unique recall + CTE overlap + 0.9 linking quality."""

from __future__ import annotations

import json
import re
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Set

PLAN_DIR = Path(__file__).resolve().parents[1] / "new30_plan"
DIV_DIR = Path(__file__).resolve().parent
OUT = Path(__file__).resolve().parents[2]

sys.path.insert(0, str(PLAN_DIR))
import metrics as met  # noqa: E402


def load_merged(stem: str) -> dict:
    p = OUT / f"{stem}.json"
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    data = {}
    for i in range(4):
        sp = OUT / f"{stem}_w{i}.json"
        if sp.exists():
            data.update(json.loads(sp.read_text(encoding="utf-8")))
    return data


def cte_hashes_by_temp(rec: Dict[str, Any]) -> Dict[float, Set[str]]:
    out: Dict[float, Set[str]] = defaultdict(set)
    for tr in rec.get("decompose_expand_traces") or []:
        for item in tr.get("diverse_kept") or []:
            t = item.get("temperature")
            sig = item.get("structure_sig")
            if t is not None and sig:
                out[float(t)].add(sig)
        for audit in tr.get("call_audits") or []:
            ss = (audit.get("schema_strategy") or {})
            t = ss.get("temp") or audit.get("temperature")
            for item in audit.get("candidates") or []:
                sig = item.get("structure_sig")
                if t is not None and sig:
                    out[float(t)].add(sig)
    return out


def recall_by_temp(rec: Dict[str, Any]) -> Dict[float, bool]:
    """Whether any correct SQL came from rollout paths tagged with temp (approx via traces)."""
    sm = met.sql_correct_map(rec)
    if not any(sm.values()):
        return {0.3: False, 0.6: False, 0.9: False}
    hashes = cte_hashes_by_temp(rec)
    # proxy: if temp produced unique hashes and question has recall, attribute loosely
    result = {}
    for t in (0.3, 0.6, 0.9):
        result[t] = bool(hashes.get(t))
    if met.has_recall(rec):
        for t in result:
            if hashes.get(t):
                result[t] = True
    return result


def overlap(a: Set[str], b: Set[str]) -> float:
    if not a:
        return 0.0
    return len(a & b) / len(a)


def extract_tables_from_gold(sql: str) -> Set[str]:
    if not sql:
        return set()
    return {m.lower() for m in re.findall(r"(?:from|join)\s+`?(\w+)`?", sql, re.I)}


def main() -> None:
    e0 = json.loads((PLAN_DIR / "e0_bprime_baseline.json").read_text(encoding="utf-8"))
    div = load_merged("v4_schema_div_30q_coder_rollouts12")
    manifest = json.loads((PLAN_DIR / "manifest.json").read_text(encoding="utf-8"))
    qids = [r["qid"] for r in manifest["questions"]]

    rows = []
    only_06 = only_09 = 0
    link_prec_sum = link_rec_sum = link_n = 0

    for qid in qids:
        if qid not in div:
            continue
        rec = div[qid]
        ev = met.eval_record(rec)
        e0q = e0["per_question"].get(qid, {})
        hashes = cte_hashes_by_temp(rec)
        h03, h06, h09 = hashes.get(0.3, set()), hashes.get(0.6, set()), hashes.get(0.9, set())
        only06 = bool(h06 - h03 - h09) and met.has_recall(rec)
        only09 = bool(h09 - h03 - h06) and met.has_recall(rec)
        if only06:
            only_06 += 1
        if only09:
            only_09 += 1

        gold_sql = (rec.get("stats") or {}).get("gold_sql") or ""
        gold_tables = extract_tables_from_gold(gold_sql)
        for tr in rec.get("decompose_expand_traces") or []:
            for ss in tr.get("per_temp_schema_strategy") or []:
                if ss.get("temp") != 0.9 or not ss.get("linking_ok"):
                    continue
                sel = {t.lower() for t in (ss.get("closed_tables") or ss.get("selected_tables") or [])}
                if gold_tables and sel:
                    link_n += 1
                    link_prec_sum += len(gold_tables & sel) / max(len(sel), 1)
                    link_rec_sum += len(gold_tables & sel) / max(len(gold_tables), 1)

        rows.append(
            {
                "qid": qid,
                "recall": ev["recall"],
                "hit1_r3": ev["hit1_r3"],
                "e0_recall": e0q.get("recall"),
                "e0_hit1": e0q.get("hit1_r3"),
                "overlap_03_06": overlap(h03, h06),
                "overlap_03_09": overlap(h03, h09),
                "n_03": len(h03),
                "n_06": len(h06),
                "n_09": len(h09),
            }
        )

    n = len(rows)
    recall = sum(r["recall"] for r in rows)
    hit1 = sum(r["hit1_r3"] for r in rows)
    e0_recall = sum(r["e0_recall"] or 0 for r in rows)
    e0_hit1 = sum(r["e0_hit1"] or 0 for r in rows)

    div_md = DIV_DIR / "per_temp_diversity.md"
    link_md = DIV_DIR / "linking_quality.md"
    div_lines = [
        "# Schema Diversity — hard30 report",
        f"Generated: {datetime.now(timezone.utc).isoformat()}",
        "",
        f"Questions: **{n}/30**",
        "",
        "## vs E0 B′",
        "",
        f"- Recall: **{recall}/{n}** (E0 {e0_recall}/{n}, Δ {recall - e0_recall:+d})",
        f"- Hit@1 R3: **{hit1}/{n}** (E0 {e0_hit1}/{n}, Δ {hit1 - e0_hit1:+d})",
        "",
        "## Diversity diagnostics",
        "",
        f"- Questions with 0.6-unique CTE hashes (proxy): **{only_06}**",
        f"- Questions with 0.9-unique CTE hashes (proxy): **{only_09}**",
        f"- Mean overlap 0.3 vs 0.6: **{sum(r['overlap_03_06'] for r in rows)/max(n,1):.2f}**",
        f"- Mean overlap 0.3 vs 0.9: **{sum(r['overlap_03_09'] for r in rows)/max(n,1):.2f}**",
        "",
        "| qid | Δrecall | Δhit1 | |h0.3| |h0.6| |h0.9| | ov03-06 | ov03-09 |",
        "|-----|---------|-------|------|------|------|---------|---------|",
    ]
    for r in rows:
        dr = int(r["recall"]) - int(r["e0_recall"] or 0)
        dh = int(r["hit1_r3"]) - int(r["e0_hit1"] or 0)
        div_lines.append(
            f"| {r['qid']} | {dr:+d} | {dh:+d} | {r['n_03']} | {r['n_06']} | {r['n_09']} | {r['overlap_03_06']:.2f} | {r['overlap_03_09']:.2f} |"
        )
    div_md.write_text("\n".join(div_lines) + "\n", encoding="utf-8")

    link_lines = [
        "# 0.9 Schema Linking Quality",
        "",
        f"Linking steps with gold SQL: **{link_n}**",
        f"Mean precision: **{link_prec_sum/link_n:.2f}**" if link_n else "Mean precision: N/A",
        f"Mean recall: **{link_rec_sum/link_n:.2f}**" if link_n else "Mean recall: N/A",
    ]
    link_md.write_text("\n".join(link_lines) + "\n", encoding="utf-8")
    print(div_md.read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()
