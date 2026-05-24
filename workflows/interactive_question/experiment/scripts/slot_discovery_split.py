#!/usr/bin/env python3
"""Lock dev/test split for Stage 1 slot discovery (Path B)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

SPLIT_OUT = ROOT / "experiment/results/slot_discovery_split.json"
VALIDATION_JSON = ROOT / "experiment/results/slot_discovery_validation.json"


def load_qids() -> list[str]:
    if VALIDATION_JSON.exists():
        data = json.loads(VALIDATION_JSON.read_text())
        return sorted(data["qids"], key=int)
    raise FileNotFoundError(f"Missing {VALIDATION_JSON}")


def make_split(qids: list[str]) -> dict[str, list[str]]:
    dev: list[str] = []
    test: list[str] = []
    for i, qid in enumerate(qids):
        if i % 2 == 0:
            dev.append(qid)
        else:
            test.append(qid)
    return {"dev": dev, "test": test, "all": qids, "rule": "sorted qid asc; even index->dev, odd->test"}


def main() -> None:
    qids = load_qids()
    if len(qids) != 30:
        print(f"WARNING: expected 30 qids, got {len(qids)}", file=sys.stderr)
    split = make_split(qids)
    SPLIT_OUT.parent.mkdir(parents=True, exist_ok=True)
    SPLIT_OUT.write_text(json.dumps(split, indent=2), encoding="utf-8")
    print(json.dumps(split, indent=2))
    print(f"Wrote {SPLIT_OUT}")


if __name__ == "__main__":
    main()
