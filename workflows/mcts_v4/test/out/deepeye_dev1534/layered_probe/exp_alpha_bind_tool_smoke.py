#!/usr/bin/env python3
"""Parallel branch smoke: Alpha-SQL schema_bind TOOL only (no freechain / DeepEye loop).

Validates whether Alpha column selection picks gold-relevant columns on hard qids
(e.g. 28 → schools.FundingType / DOC / School, not frpm.Charter Funding Type).

Keep freechain_smoke_v1 running untouched.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml
from openai import OpenAI

ROOT = Path("/hpc2hdd/home/sshen190/wtao565/SelfCorrectionSQL")
sys.path.insert(0, str(ROOT))

_LP = Path(__file__).resolve().parent
if str(_LP) not in sys.path:
    sys.path.insert(0, str(_LP))

from alpha_bind_tools import bind_for_cte  # noqa: E402
from workflows.mcts_v4.actions.deepeye_snapshot_context import load_deepeye_context  # noqa: E402
from workflows.mcts_v4.utils.llm_chat import create_chat_completion  # noqa: E402

GOLD_PATH = Path("/hpc2hdd/home/sshen190/wtao565/datasets/dev_20240627/dev.json")
DEFAULT_QIDS = ["28", "94", "1036"]


def _apply_preset(preset: str) -> dict:
    cfg = yaml.safe_load(
        (ROOT / "workflows/interactive_question/LLM/config.yaml").read_text(encoding="utf-8")
    )["llm_presets"][preset]
    os.environ["VLLM_API_URL"] = cfg["base_url"]
    os.environ["VLLM_API_KEY"] = cfg["api_key"]
    os.environ["VLLM_MODEL"] = cfg["model"]
    if cfg.get("enable_thinking") is False:
        os.environ["VLLM_ENABLE_THINKING"] = "0"
    return cfg


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--preset", default="local_8000")
    ap.add_argument("--qids", default=",".join(DEFAULT_QIDS))
    ap.add_argument("--with-value-id", action="store_true")
    ap.add_argument(
        "--out",
        type=Path,
        default=_LP / "results" / "alpha_bind_tool_smoke.json",
    )
    args = ap.parse_args()

    cfg = _apply_preset(args.preset)
    client = OpenAI(base_url=cfg["base_url"], api_key=cfg["api_key"], timeout=120)
    model = cfg["model"]

    def llm_complete(prompt: str, temperature: float = 0.1) -> str:
        resp = create_chat_completion(
            client,
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=temperature,
        )
        return resp.choices[0].message.content or ""

    gold_rows = json.loads(GOLD_PATH.read_text(encoding="utf-8"))
    gold = {str(r.get("question_id", i)): r for i, r in enumerate(gold_rows)}
    qids = [q.strip() for q in args.qids.split(",") if q.strip()]

    rows = []
    print(f"[alpha-bind-tool] n={len(qids)} model={model}", flush=True)
    for qid in qids:
        g = gold[qid]
        ctx = load_deepeye_context(qid)
        it = ctx.get("item") or {}
        schema = (
            it.get("schema_ddl")
            or it.get("ddl_data")
            or it.get("simplified_ddl")
            or it.get("schema")
            or ctx.get("schema_profile")
            or ""
        )
        pack = bind_for_cte(
            question=g["question"],
            evidence=g.get("evidence") or "",
            schema_context=schema,
            llm_complete=llm_complete,
            with_value_id=bool(args.with_value_id),
        )
        rec = {
            "qid": qid,
            "db_id": g["db_id"],
            "ok": pack.ok,
            "error": pack.error,
            "selected_columns": pack.selected_columns,
            "binding_hint": pack.binding_hint,
            "gold_sql": (g.get("SQL") or g.get("query") or "")[:500],
        }
        print(
            f"  qid={qid} ok={pack.ok} cols={pack.selected_columns} err={pack.error}",
            flush=True,
        )
        rows.append(rec)

    out = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "branch": "exp/alpha-bind-cte-tool",
        "summary": {"n": len(rows), "ok": sum(1 for r in rows if r["ok"])},
        "rows": rows,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(out["summary"], ensure_ascii=False), flush=True)
    print(f"[done] {args.out}", flush=True)


if __name__ == "__main__":
    main()
