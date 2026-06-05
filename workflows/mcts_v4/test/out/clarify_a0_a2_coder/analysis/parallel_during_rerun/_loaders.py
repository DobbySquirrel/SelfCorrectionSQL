"""Shared loaders for parallel_during_rerun analysis (read-only)."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Dict, List, Set, Tuple

ROOT = Path(__file__).resolve().parents[7]  # .../SelfCorrectionSQL
OUT_BASE = ROOT / "workflows/mcts_v4/test/out/clarify_a0_a2_coder"
PAR_DIR = Path(__file__).resolve().parent
GOLD_FILE = ROOT / "workflows/mcts_v3/data/arcwise_plat_sql_only_with_diff_withSchema.json"
FINAL_PATH = OUT_BASE / "v4_final_498q_coder_rollouts8.json"
BASE_PATH = ROOT / "workflows/mcts_v4/test/out/v4_arcwise_full_result_rollouts_20.json"
EF2_FILE = OUT_BASE / "qids_ef2_51.json"
PPL_FILE = ROOT / "workflows/mcts_v1/test/out/arcwise_alpha_sql_ppl_rollouts_20_3_15_nigga.json"
A0_8 = OUT_BASE / "v4_a0_30q_coder_rollouts8.json"
A3_8 = OUT_BASE / "v4_a3_30q_coder_rollouts8.json"
EF2_RERUN = OUT_BASE / "v4_ef2_51_rerun_coder_rollouts8.json"


def load_json(p: Path) -> dict:
    return json.loads(p.read_text(encoding="utf-8"))


def hit1(rec: dict) -> bool:
    return bool((rec.get("stats") or {}).get("gold_match"))


def load_ef2() -> Set[str]:
    return {str(q) for q in load_json(EF2_FILE).get("qids", [])}


def load_gold_meta() -> Tuple[Dict[str, str], Dict[str, str]]:
    gold_sqls, qid_to_db = {}, {}
    for item in load_json(GOLD_FILE):
        qid = str(item.get("question_id"))
        gold_sqls[qid] = item.get("SQL", "") or ""
        qid_to_db[qid] = (item.get("db_id") or item.get("db") or "").strip()
    return gold_sqls, qid_to_db


def norm_sql(sql: str) -> str:
    return re.sub(r"\s+", " ", (sql or "").strip().lower())


def load_merged_498() -> dict:
    fin = load_json(FINAL_PATH)
    ef2 = load_ef2()
    if EF2_RERUN.exists():
        rerun = load_json(EF2_RERUN)
        for q in ef2:
            fin[str(q)] = rerun[str(q)]
    return fin
