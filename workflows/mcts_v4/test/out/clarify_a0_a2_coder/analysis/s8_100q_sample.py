#!/usr/bin/env python3
"""Stratified 100q sample for AutoClarify real_llm replay (no MCTS, no real_llm)."""
from __future__ import annotations

import hashlib
import json
import random
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Set

ROOT = Path(__file__).resolve().parents[6]
ANALYSIS = Path(__file__).resolve().parent

P0_JSON = ANALYSIS / "p0_union_recall.json"
D2B_JSON = ANALYSIS / "d2b_g4_498_replay.json"
S7_QIDS = ROOT / "workflows/mcts_v4/test/out/clarify_a0_a2_coder/s7_41_qids.txt"
OUT_TXT = ANALYSIS / "s8_100q_qids.txt"
OUT_MD = ANALYSIS / "s8_100q_sampling.md"
OUT_BUCKETS_JSON = ANALYSIS / "s8_100q_buckets.json"

SEED = 20240601
K_MISSED = 30
K_S7 = 16  # s7 pool after dedup is 16; do not inflate first 3 buckets
K_R2 = 27  # 100 - 9 - 18 - 30 - 16


def load_s7() -> List[str]:
    return [ln.strip() for ln in S7_QIDS.read_text().splitlines() if ln.strip()]


def load_r2_hit_merged() -> Set[str]:
    data = json.loads(D2B_JSON.read_text(encoding="utf-8"))
    return {str(r["qid"]) for r in data.get("rows", []) if r.get("r2_hit")}


def sample_list(pool: List[str], k: int, rng: random.Random) -> List[str]:
    if k > len(pool):
        raise ValueError(f"sample k={k} > pool size {len(pool)}")
    return sorted(rng.sample(pool, k), key=int)


def sha256_qids(qids: List[str]) -> str:
    payload = "\n".join(sorted(qids, key=int)) + "\n"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def build_buckets() -> Dict[str, List[str]]:
    """Deterministic 100q stratified buckets (same logic as main)."""
    p0 = json.loads(P0_JSON.read_text(encoding="utf-8"))
    ex = p0["exclusive"]
    calib_only = sorted(ex["calib_only"], key=int)
    final_only = sorted(ex["final_only"], key=int)
    missed_pool = sorted(ex["missed_by_all"], key=int)
    s7_all = load_s7()
    r2_hit = load_r2_hit_merged()

    rng = random.Random(SEED)
    used: Set[str] = set()
    buckets: Dict[str, List[str]] = {}

    def take(name: str, qids: List[str]) -> None:
        overlap = set(qids) & used
        if overlap:
            raise RuntimeError(f"{name} overlaps prior buckets: {sorted(overlap, key=int)}")
        buckets[name] = qids
        used.update(qids)

    take("calib_only", calib_only)
    take("final_only", final_only)
    take("missed_by_all", sample_list(missed_pool, K_MISSED, rng))

    s7_pool = sorted([q for q in s7_all if q not in used], key=int)
    k_s7 = min(K_S7, len(s7_pool))
    s7_shortfall = max(0, K_S7 - k_s7)
    if k_s7 == 0:
        raise ValueError("S7 pool empty after excluding first 3 buckets")
    take("S7_subset", sample_list(s7_pool, k_s7, rng) if k_s7 < len(s7_pool) else s7_pool)

    r2_pool = sorted([q for q in r2_hit if q not in used], key=int)
    k_r2 = K_R2 + s7_shortfall
    if len(r2_pool) < k_r2:
        raise ValueError(f"R2_hit pool {len(r2_pool)} < {k_r2} (need {K_R2}+{s7_shortfall} top-up)")
    take("R2_hit_random", sample_list(r2_pool, k_r2, rng))

    all_qids = sorted(used, key=int)
    if len(all_qids) != 100:
        raise RuntimeError(f"expected 100 qids, got {len(all_qids)}")

    # cross-bucket overlap check
    for a, qa in buckets.items():
        for b, qb in buckets.items():
            if a >= b:
                continue
            inter = set(qa) & set(qb)
            if inter:
                raise RuntimeError(f"overlap {a} ∩ {b}: {inter}")

    return buckets, all_qids, {
        "missed_pool_size": len(missed_pool),
        "s7_shortfall": s7_shortfall,
        "k_s7": k_s7,
        "k_r2": k_r2,
    }


def main() -> None:
    buckets, all_qids, meta = build_buckets()
    missed_pool = sorted(
        json.loads(P0_JSON.read_text(encoding="utf-8"))["exclusive"]["missed_by_all"],
        key=int,
    )
    s7_shortfall = meta["s7_shortfall"]
    k_s7 = meta["k_s7"]
    k_r2 = meta["k_r2"]

    digest = sha256_qids(all_qids)
    OUT_TXT.write_text("\n".join(all_qids) + "\n", encoding="utf-8")
    OUT_BUCKETS_JSON.write_text(
        json.dumps({"seed": SEED, "buckets": buckets}, indent=2),
        encoding="utf-8",
    )

    lines = [
        "# S8 — Stratified 100q Sample (AutoClarify real_llm)",
        "",
        f"Generated: {datetime.now().isoformat(timespec='seconds')}",
        f"Seed: **{SEED}**",
        "",
        "## Bucket counts",
        "",
        "| Bucket | Rule | n |",
        "|---|---|---:|",
        f"| calib_only | P0 exclusive, **all** | {len(buckets['calib_only'])} |",
        f"| final_only | P0 exclusive, **all** | {len(buckets['final_only'])} |",
        f"| missed_by_all | P0 pool n={len(missed_pool)}, `random.sample(k={K_MISSED})` | {len(buckets['missed_by_all'])} |",
        f"| S7_subset | s7_41 minus prior buckets, `k={K_S7}` (available={k_s7}) | {len(buckets['S7_subset'])} |",
        f"| R2_hit_random | merged R2 hit minus prior, `k={k_r2}` | {len(buckets['R2_hit_random'])} |",
        f"| **Total** | | **{len(all_qids)}** |",
        "",
        "## SHA256 (qid list, sorted)",
        "",
        f"```",
        f"{digest}",
        f"```",
        "",
        f"File: `{OUT_TXT.relative_to(ROOT)}`",
        "",
    ]
    s7_all = load_s7()
    in_first_three = len(s7_all) - len(
        [q for q in s7_all if q not in set(buckets["calib_only"] + buckets["final_only"] + buckets["missed_by_all"])]
    )
    if s7_shortfall:
        lines += [
            f"> ⚠️ S7 去重后仅 **{k_s7}** 题可用（s7_41 中 **{in_first_three}** 题已落入前三桶，目标 k={K_S7}）。",
            f"> 实际 S7 **{len(buckets['S7_subset'])}** 题；R2 桶 **+{s7_shortfall}** → **{k_r2}** 题，合计仍 **100**。",
            "",
        ]
    lines += [
        "## Per-bucket qids",
        "",
    ]
    for name in (
        "calib_only",
        "final_only",
        "missed_by_all",
        "S7_subset",
        "R2_hit_random",
    ):
        qids = buckets[name]
        lines.append(f"### {name} ({len(qids)})")
        lines.append("")
        lines.append(f"`{', '.join(qids)}`")
        lines.append("")

    lines += [
        "## Overlap confirmation",
        "",
        "✅ **Zero overlap** between buckets (pairwise intersection empty).",
        "",
        "## Inputs",
        "",
        f"- `{P0_JSON.relative_to(ROOT)}` — exclusive buckets",
        f"- `{S7_QIDS.relative_to(ROOT)}` — 41 S7 qids",
        f"- `{D2B_JSON.relative_to(ROOT)}` — merged R2 replay `rows[].r2_hit` (n={len(load_r2_hit_merged())})",
        "",
        "🛑 **Do not** run real_llm or MCTS until smoke plan is approved.",
    ]
    OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(OUT_TXT)
    print(OUT_MD)
    print(f"sha256={digest}")
    print(f"total={len(all_qids)} buckets={{{', '.join(f'{k}:{len(v)}' for k,v in buckets.items())}}}")


if __name__ == "__main__":
    main()
