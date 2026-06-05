#!/usr/bin/env python3
"""20q smoke subset: first N per bucket from s8_100q (deterministic, smoke ⊂ full)."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Dict, List

ANALYSIS = Path(__file__).resolve().parent
FULL_TXT = ANALYSIS / "s8_100q_qids.txt"
BUCKETS_JSON = ANALYSIS / "s8_100q_buckets.json"
OUT_TXT = ANALYSIS / "s8_20q_smoke_qids.txt"
OUT_MD = ANALYSIS / "s8_20q_smoke_sampling.md"

SMOKE_TAKE = {
    "calib_only": 2,
    "final_only": 4,
    "missed_by_all": 6,
    "S7_subset": 3,
    "R2_hit_random": 5,
}


def sha256_qids(qids: List[str]) -> str:
    payload = "\n".join(sorted(qids, key=int)) + "\n"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def main() -> None:
    if not BUCKETS_JSON.is_file():
        raise SystemExit(f"missing {BUCKETS_JSON} — run s8_100q_sample.py first")

    data = json.loads(BUCKETS_JSON.read_text(encoding="utf-8"))
    buckets: Dict[str, List[str]] = data["buckets"]
    full_set = set(FULL_TXT.read_text().splitlines())

    smoke_buckets: Dict[str, List[str]] = {}
    smoke_all: List[str] = []
    used = set()

    for name, k in SMOKE_TAKE.items():
        pool = buckets.get(name) or []
        if k > len(pool):
            raise ValueError(f"{name}: need {k}, have {len(pool)}")
        pick = pool[:k]
        overlap = set(pick) & used
        if overlap:
            raise RuntimeError(f"overlap in {name}: {overlap}")
        smoke_buckets[name] = pick
        used.update(pick)
        smoke_all.extend(pick)

    if len(smoke_all) != sum(SMOKE_TAKE.values()):
        raise RuntimeError("smoke count mismatch")

    if not set(smoke_all) <= full_set:
        extra = set(smoke_all) - full_set
        raise RuntimeError(f"smoke not subset of full: {extra}")

    digest = sha256_qids(smoke_all)
    OUT_TXT.write_text("\n".join(smoke_all) + "\n", encoding="utf-8")

    lines = [
        "# S8 — 20q Smoke Subset",
        "",
        f"Generated: {datetime.now().isoformat(timespec='seconds')}",
        "",
        "## Rule",
        "",
        "Per bucket: **first N** qids in `s8_100q_buckets.json` order (no re-sample).",
        "",
        "## Bucket counts",
        "",
        "| Bucket | 100q | smoke |",
        "|---|---:|---:|",
    ]
    for name, k in SMOKE_TAKE.items():
        lines.append(f"| {name} | {len(buckets[name])} | {k} |")
    lines.append(f"| **Total** | 100 | **{len(smoke_all)}** |")
    lines += [
        "",
        "## SHA256 (sorted qid list)",
        "",
        f"```",
        f"{digest}",
        f"```",
        "",
        f"File: `{OUT_TXT.name}`",
        "",
        "## smoke ⊂ full",
        "",
        f"✅ All **{len(smoke_all)}** smoke qids ∈ `s8_100q_qids.txt`",
        "",
        "## Per-bucket qids (smoke order)",
        "",
    ]
    for name, qids in smoke_buckets.items():
        lines.append(f"### {name} ({len(qids)})")
        lines.append("")
        lines.append(f"`{', '.join(qids)}`")
        lines.append("")

    lines.append("🛑 Do not run real_llm until replay wiring confirmed.")
    OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(OUT_TXT)
    print(OUT_MD)
    print(f"sha256={digest} total={len(smoke_all)}")


if __name__ == "__main__":
    main()
