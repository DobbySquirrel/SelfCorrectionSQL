#!/usr/bin/env python3
"""Task 1.5: compare A0 instrumentation run vs baseline acc + hit pattern Jaccard."""

import argparse
import json
import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def hit_sets(data: dict) -> dict:
    out = {}
    for qid, rec in data.items():
        gm = (rec.get("stats") or {}).get("gold_match")
        if gm is not None:
            out[qid] = bool(gm)
    return out


def jaccard(a: dict, b: dict) -> float:
    keys = set(a) | set(b)
    if not keys:
        return 1.0
    inter = sum(1 for k in keys if a.get(k) == b.get(k) and k in a and k in b)
    return inter / len(keys)


def run_acc(result_file: Path, gold_file: Path, out_json: Path) -> dict:
    cmd = [
        sys.executable,
        str(Path(__file__).resolve().parent.parent / "test" / "compute_arcwise_any_path_acc.py"),
        "--result_file",
        str(result_file),
        "--gold_file",
        str(gold_file),
        "--json_out",
        str(out_json),
    ]
    subprocess.run(cmd, check=True)
    raw = json.loads(out_json.read_text(encoding="utf-8"))
    # normalize keys from compute_arcwise_any_path_acc summary
    if "hit1_accuracy_pct" in raw:
        raw["hit_at_1_pct"] = raw["hit1_accuracy_pct"]
        raw["any_path_pct"] = raw["any_accuracy_pct"]
    return raw


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--new", type=Path, required=True)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--gold_file", type=Path, required=True)
    parser.add_argument("--acc_new", type=Path, required=True)
    parser.add_argument("--acc_baseline", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--tol_pp", type=float, default=2.0)
    parser.add_argument("--baseline_rollouts", type=int, default=8)
    args = parser.parse_args()

    new_data = json.loads(args.new.read_text(encoding="utf-8"))
    base_data = json.loads(args.baseline.read_text(encoding="utf-8"))
    qids = list(new_data.keys())

    base_sub = {q: base_data[q] for q in qids if q in base_data}
    acc_n = run_acc(args.new, args.gold_file, args.acc_new)
    acc_b = run_acc(Path(args.baseline), args.gold_file, args.acc_baseline)

    h1n = acc_n.get("hit_at_1_pct", acc_n.get("hit_at_1", 0))
    h1b = acc_b.get("hit_at_1_pct", acc_b.get("hit_at_1", 0))
    apn = acc_n.get("any_path_pct", acc_n.get("any_path", 0))
    apb = acc_b.get("any_path_pct", acc_b.get("any_path", 0))
    jac = jaccard(hit_sets(new_data), hit_sets(base_sub))

    tol = args.tol_pp if args.baseline_rollouts == 8 else max(args.tol_pp, 4.0)
    ok_h1 = abs(h1n - h1b) <= tol
    ok_ap = abs(apn - apb) <= tol
    ok_j = jac >= 0.85
    ok = ok_h1 and ok_ap and ok_j

    lines = [
        "# A0 zero-regression acc sanity\n",
        f"- Baseline: `{args.baseline}` (rollouts={args.baseline_rollouts})",
        f"- New: `{args.new}`",
        f"- Tolerance: ±{tol}pp, Jaccard ≥ 0.85\n",
        "| Metric | Baseline | A0 | Δ | Pass |",
        "|---|---:|---:|---:|:---:|",
        f"| Hit@1 | {h1b:.2f}% | {h1n:.2f}% | {h1n-h1b:+.2f}pp | {'OK' if ok_h1 else 'FAIL'} |",
        f"| any_path | {apb:.2f}% | {apn:.2f}% | {apn-apb:+.2f}pp | {'OK' if ok_ap else 'FAIL'} |",
        f"| Hit pattern Jaccard | — | — | {jac:.3f} | {'OK' if ok_j else 'FAIL'} |",
        f"\n**Overall:** {'PASS' if ok else 'FAIL — stop and revert'}\n",
    ]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("\n".join(lines), encoding="utf-8")
    print(args.output)
    if not ok:
        sys.exit(1)


if __name__ == "__main__":
    main()
