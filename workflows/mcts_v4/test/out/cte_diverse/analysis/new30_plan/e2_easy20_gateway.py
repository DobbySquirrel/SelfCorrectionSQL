#!/usr/bin/env python3
"""E2 easy20 + 50q combined gateway (E0 hard30 + E1 + E2 easy20)."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import metrics as met  # noqa: E402

OUT = Path(__file__).resolve().parents[2]
PLAN = Path(__file__).resolve().parent


def load_b2_498() -> dict:
    data = {}
    for i in range(4):
        p = OUT / f"v4_diverse_b2_n3_sv5_498q_coder_rollouts12_w{i}.json"
        data.update(json.loads(p.read_text(encoding="utf-8")))
    return data


def load_json(path: Path) -> dict:
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    data = {}
    base = path.stem
    for i in range(4):
        p = path.parent / f"{base}_w{i}.json"
        if p.exists():
            data.update(json.loads(p.read_text(encoding="utf-8")))
    return data


def eval_plan_rec(rec: dict) -> dict:
    rss = rec.get("union_rollout_stats") or rec.get("rollout_stats") or []
    fake = {**rec, "rollout_stats": rss}
    return met.eval_record(fake)


def main() -> None:
    easy_q = [ln.strip() for ln in (PLAN / "qids_easy20.txt").read_text().splitlines() if ln.strip()]
    hard_q = [ln.strip() for ln in (PLAN / "qids.txt").read_text().splitlines() if ln.strip()]
    b2 = load_b2_498()
    e1 = load_json(OUT / "v4_plan_e1_new30_coder_rollouts12.json")
    e2 = load_json(OUT / "v4_plan_e2_easy20_coder_rollouts12.json")

    if len(e2) < len(easy_q):
        print(f"E2 incomplete: {len(e2)}/{len(easy_q)} — run merge first")
        return

    rows_e2 = []
    hurt = []
    saved = []
    for qid in easy_q:
        e0 = met.eval_record(b2[qid])
        e2ev = eval_plan_rec(e2[qid])
        row = {"qid": qid, "e0": e0, "e2": e2ev}
        rows_e2.append(row)
        if e0["hit1_r3"] and not e2ev["hit1_r3"]:
            hurt.append(qid)
        if not e0["hit1_r3"] and e2ev["hit1_r3"]:
            saved.append(qid)

    n = len(easy_q)
    e0_hit = sum(int(r["e0"]["hit1_r3"]) for r in rows_e2)
    e2_hit = sum(int(r["e2"]["hit1_r3"]) for r in rows_e2)
    e0_rec = sum(int(r["e0"]["recall"]) for r in rows_e2)
    e2_rec = sum(int(r["e2"]["recall"]) for r in rows_e2)

    # 50q: hard30 E1 vs E0 + easy20 E2 vs E0
    hard_e0_hit = sum(1 for q in hard_q if met.hit1(b2.get(q, {}), "R3"))
    hard_e1_hit = sum(1 for q in hard_q if q in e1 and eval_plan_rec(e1[q])["hit1_r3"])
    net_hit_50 = (hard_e1_hit - hard_e0_hit) + (e2_hit - e0_hit)
    hard_e0_rec = sum(1 for q in hard_q if met.has_recall(b2.get(q, {})))
    hard_e1_rec = sum(1 for q in hard_q if q in e1 and eval_plan_rec(e1[q])["recall"])
    net_rec_50 = (hard_e1_rec - hard_e0_rec) + (e2_rec - e0_rec)

    gates = {
        "hit1_net_50": net_hit_50 >= 5,
        "recall_net_50": net_rec_50 >= 0,
        "easy_hurt": len(hurt) <= 2,
        "d_root_cause": True,
    }
    decision = "GO_498" if all(gates.values()) else "STOP_498"

    out = {
        "generated": datetime.now(timezone.utc).isoformat(),
        "easy20": {
            "n": n,
            "e0_hit1": e0_hit,
            "e2_hit1": e2_hit,
            "delta_hit1": e2_hit - e0_hit,
            "e0_recall": e0_rec,
            "e2_recall": e2_rec,
            "delta_recall": e2_rec - e0_rec,
            "hurt": hurt,
            "saved": saved,
        },
        "combined_50q": {
            "hard30_e0_hit": hard_e0_hit,
            "hard30_e1_hit": hard_e1_hit,
            "easy20_e0_hit": e0_hit,
            "easy20_e2_hit": e2_hit,
            "net_hit1": net_hit_50,
            "net_recall": net_rec_50,
        },
        "gates": gates,
        "decision": decision,
        "per_question": rows_e2,
    }
    (PLAN / "e2_easy20_gateway.json").write_text(
        json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    md = [
        "# E2 easy20 + 50q gateway",
        "",
        f"Generated: {out['generated']}",
        "",
        "## Easy20 (Bucket E)",
        "",
        f"| | Hit@1 R3 | Recall |",
        f"|---|---:|---:|",
        f"| E0 B′ | {e0_hit}/{n} | {e0_rec}/{n} |",
        f"| E2 plan | {e2_hit}/{n} | {e2_rec}/{n} |",
        f"| Δ | {e2_hit - e0_hit:+d} | {e2_rec - e0_rec:+d} |",
        "",
        f"**Hurt** (E0 hit → E2 miss): {hurt or 'none'} ({len(hurt)}/{n})",
        f"**Saved** (E0 miss → E2 hit): {saved or 'none'}",
        "",
        "## 50q combined",
        "",
        f"- Hard30: Hit@1 {hard_e0_hit}→{hard_e1_hit} (Δ{hard_e1_hit - hard_e0_hit:+d})",
        f"- Easy20: Hit@1 {e0_hit}→{e2_hit} (Δ{e2_hit - e0_hit:+d})",
        f"- **Net Hit@1**: {net_hit_50:+d} (need ≥+5)",
        f"- **Net Recall**: {net_rec_50:+d} (need ≥0)",
        "",
        "## Gates",
        "",
        f"| gate | pass |",
        f"|---|---|",
    ]
    for k, v in gates.items():
        md.append(f"| {k} | {v} |")
    md.append("")
    md.append(f"**Decision**: `{decision}`")
    (PLAN / "e2_easy20_gateway.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print(f"E2 easy20: hurt={len(hurt)} saved={len(saved)} net50_hit={net_hit_50} -> {decision}")


if __name__ == "__main__":
    main()
