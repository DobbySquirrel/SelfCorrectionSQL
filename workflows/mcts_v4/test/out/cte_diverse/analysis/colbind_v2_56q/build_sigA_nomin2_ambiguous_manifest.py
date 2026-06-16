#!/usr/bin/env python3
"""Build manifest of R4-gate ambiguous qids from frozen sigA nomin2 full498 JSON."""

from __future__ import annotations

import argparse
import io
import json
import os
import sys
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[7]
sys.path.insert(0, str(ROOT))

from workflows.mcts_v4.utils.gated_selection import _analyze_r4_gate  # noqa: E402

HERE = Path(__file__).resolve().parent
DEFAULT_INPUT = (
    ROOT
    / "workflows/mcts_v4/test/out/cte_diverse/v4_colbind_v2_dual03_abl5_sigA_nomin2_full498_r12.json"
)
DEFAULT_OUT = HERE / "qids_sigA_nomin2_ambiguous69_manifest.json"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    ap.add_argument("--output", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--vote-margin", type=float, default=float(os.environ.get("MCTS_R4_GATE_MARGIN", "0.7")))
    args = ap.parse_args()

    data = json.loads(args.input.read_text(encoding="utf-8"))
    rows = []
    for qid in sorted(data.keys(), key=int):
        rss = (data[qid] or {}).get("rollout_stats") or []
        with redirect_stdout(io.StringIO()):
            gate = _analyze_r4_gate(rss, args.vote_margin)
        if not gate.ambiguous:
            continue
        rows.append(
            {
                "qid": str(qid),
                "gate_reason": gate.gate_reason,
                "gate_sigs": gate.gate_sigs,
                "top_votes": gate.ranked_votes[:3],
            }
        )

    out = {
        "source": str(args.input),
        "vote_margin": args.vote_margin,
        "n_ambiguous": len(rows),
        "n_total": len(data),
        "qids": [r["qid"] for r in rows],
        "per_qid": rows,
    }
    args.output.write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"ambiguous={len(rows)}/{len(data)} margin={args.vote_margin} -> {args.output}")


if __name__ == "__main__":
    main()
