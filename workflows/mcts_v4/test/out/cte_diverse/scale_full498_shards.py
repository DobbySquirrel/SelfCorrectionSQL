#!/usr/bin/env python3
"""Rebalance full498: merge done → checkpoint; split ONLY remaining qids → N shards."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--shard-dir", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--shard-basename", required=True)
    ap.add_argument("--new-shards", type=int, default=8)
    ap.add_argument("--roll-outs", type=int, default=12)
    args = ap.parse_args()

    manifest_path = Path(args.manifest)
    shard_dir = Path(args.shard_dir)
    out_dir = Path(args.out_dir)
    shard_dir.mkdir(parents=True, exist_ok=True)

    qids = [str(q) for q in json.loads(manifest_path.read_text(encoding="utf-8"))["qids"]]
    done: dict = {}
    old_shards = 0
    i = 0
    while True:
        p = out_dir / f"{args.shard_basename}_w{i}.json"
        if not p.is_file():
            break
        done.update(json.loads(p.read_text(encoding="utf-8")))
        old_shards = i + 1
        i += 1

    checkpoint = out_dir / f"{args.shard_basename}_done_checkpoint.json"
    checkpoint.write_text(json.dumps(done, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    remaining = [q for q in qids if q not in done]
    n_rem = len(remaining)
    size = (n_rem + args.new_shards - 1) // args.new_shards if n_rem else 0
    print(
        f"[scale] done={len(done)}/{len(qids)}  remaining={n_rem}  "
        f"{old_shards} old shards → {args.new_shards} workers (remaining-only)"
    )

    for i in range(args.new_shards):
        chunk = remaining[i * size : (i + 1) * size] if size else []
        shard_manifest = {
            "description": f"remaining-only rollouts={args.roll_outs} shard {i}/{args.new_shards} ({len(chunk)} qids)",
            "shard": i,
            "n_shards": args.new_shards,
            "rollouts": args.roll_outs,
            "qids": chunk,
        }
        (shard_dir / f"shard{i}.json").write_text(
            json.dumps(shard_manifest, indent=2) + "\n", encoding="utf-8"
        )
        out_path = out_dir / f"{args.shard_basename}_w{i}.json"
        # in-progress results for qids still in this chunk
        subset = {q: done[q] for q in chunk if q in done}
        if subset:
            out_path.write_text(json.dumps(subset, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        elif out_path.is_file():
            out_path.unlink()
        print(f"[scale] w{i}: {len(chunk)} qids to run")

    for j in range(args.new_shards, max(old_shards, args.new_shards + 4)):
        stale = out_dir / f"{args.shard_basename}_w{j}.json"
        if stale.is_file():
            stale.unlink()
            print(f"[scale] removed stale {stale.name}")

    (shard_dir / "manifest.json").write_text(
        json.dumps(
            {"n_total": len(qids), "n_done": len(done), "n_remaining": n_rem, "n_shards": args.new_shards, "qids": qids},
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"[scale] checkpoint -> {checkpoint.name}")


if __name__ == "__main__":
    main()
