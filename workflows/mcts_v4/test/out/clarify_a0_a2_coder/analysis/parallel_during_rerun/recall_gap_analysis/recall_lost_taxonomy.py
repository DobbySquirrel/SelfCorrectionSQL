#!/usr/bin/env python3
"""R1: 75 recall-lost questions — 7-bucket taxonomy (read-only)."""
from __future__ import annotations

import json
import re
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

ROOT = Path(__file__).resolve().parents[8]  # .../SelfCorrectionSQL
OUT_BASE = Path(__file__).resolve().parents[3]  # .../clarify_a0_a2_coder
PAR = Path(__file__).resolve().parent.parent
OUT_DIR = Path(__file__).resolve().parent
GOLD_FILE = ROOT / "workflows/mcts_v3/data/arcwise_plat_sql_only_with_diff_withSchema.json"
FINAL_PATH = OUT_BASE / "v4_final_498q_coder_rollouts8.json"
BASE_PATH = ROOT / "workflows/mcts_v4/test/out/v4_arcwise_full_result_rollouts_20.json"
EF2_FILE = OUT_BASE / "qids_ef2_51.json"
EF2_RERUN = OUT_BASE / "v4_ef2_51_rerun_coder_rollouts8.json"
PPL_FILE = ROOT / "workflows/mcts_v1/test/out/arcwise_alpha_sql_ppl_rollouts_20_3_15_nigga.json"
RECALL_CACHE_MERGED = OUT_DIR / "recall_map_498_merged.json"

BUCKETS = ["S6", "S4", "S3", "S1", "S2", "S7", "S5", "S0"]
PRIORITY = ["S6", "S4", "S3", "S1", "S2", "S7", "S5", "S0"]

FIX_PATH = {
    "S6": ("paper §6 ceiling", "no", "no", "yes limitation"),
    "S4": ("DDL trim / schema linker", "maybe", "no", "infra"),
    "S3": ("prompt / evidence", "maybe", "teacher?", "prompt"),
    "S1": ("max_depth↑", "yes", "30q", "H1 param"),
    "S2": ("K↑ / temperature", "yes", "30q", "search div"),
    "S7": ("reward redesign", "yes", "teacher", "core"),
    "S5": ("reward + cluster", "yes", "teacher", "selection"),
    "S0": ("manual review", "?", "?", "residual"),
}


def load_json(p: Path) -> dict:
    return json.loads(p.read_text(encoding="utf-8"))


def hit1(rec: dict) -> bool:
    return bool((rec.get("stats") or {}).get("gold_match"))


def load_gold_meta() -> Tuple[Dict[str, str], Dict[str, str]]:
    gold_sqls, qid_to_db = {}, {}
    for item in load_json(GOLD_FILE):
        qid = str(item.get("question_id"))
        gold_sqls[qid] = item.get("SQL", "") or ""
        qid_to_db[qid] = (item.get("db_id") or item.get("db") or "").strip()
    return gold_sqls, qid_to_db


def gold_complexity(sql: str) -> str:
    if not sql:
        return "unknown"
    s = sql.upper()
    joins = len(re.findall(r"\bJOIN\b", s))
    subq = len(re.findall(r"\(\s*SELECT\b", s))
    if joins >= 4 or subq >= 2:
        return "complex"
    if joins >= 2 or subq >= 1:
        return "medium"
    return "simple"


def load_merged() -> dict:
    fin = load_json(FINAL_PATH)
    ef2 = {str(q) for q in load_json(EF2_FILE).get("qids", [])}
    if EF2_RERUN.exists():
        rerun = load_json(EF2_RERUN)
        for q in ef2:
            fin[str(q)] = rerun[str(q)]
    return fin


def load_ppl_index() -> Dict[str, dict]:
    return {str(s["question_id"]): s for s in load_json(PPL_FILE)}


def parse_sql_features(sql: str) -> dict:
    s = (sql or "").upper()
    tables = set()
    for m in re.finditer(r"\b(?:FROM|JOIN)\s+([`\"]?\w+[`\"]?)", s, re.I):
        tables.add(m.group(1).strip('`"').lower())
    if "WITH" in s:
        for m in re.finditer(r"\b(\w+)\s+AS\s*\(", s, re.I):
            tables.add(m.group(1).lower())
    cols: Set[str] = set()
    try:
        import sqlglot

        for node in sqlglot.parse_one(sql, read="sqlite").find_all(sqlglot.exp.Column):
            if node.name:
                cols.add(node.name.lower())
    except Exception:
        for m in re.finditer(r"\b(\w+)\.(\w+)\b", sql):
            cols.add(m.group(2).lower())
    cte_layers = len(re.findall(r"\b\w+\s+AS\s*\(", s)) if "WITH" in s else 0
    return {
        "tables": tables,
        "columns": cols,
        "has_window": "OVER" in s,
        "has_recursive": "RECURSIVE" in s,
        "has_lateral": "LATERAL" in s,
        "cte_layers": cte_layers,
    }


def ddl_schema_from_text(ddl: str) -> Tuple[Set[str], Set[str]]:
    tables: Set[str] = set()
    cols: Set[str] = set()
    for m in re.finditer(r"CREATE\s+TABLE\s+[`']?(\w+)[`']?", ddl, re.I):
        tables.add(m.group(1).lower())
    for m in re.finditer(
        r"[`']?(\w+)[`']?\s+(?:INTEGER|TEXT|REAL|BLOB|NUMERIC|DATE|VARCHAR|INT|FLOAT)",
        ddl,
        re.I,
    ):
        cols.add(m.group(1).lower())
    return tables, cols


def rollout_depths(rec: dict) -> int:
    rss = rec.get("rollout_stats") or []
    depths = [len(r.get("cte_path") or []) or int(r.get("leaf_depth") or 0) for r in rss]
    return max(depths) if depths else 0


def depth1_cluster_count(rec: dict) -> int:
    sigs: Set[str] = set()
    for r in rec.get("rollout_stats") or []:
        nodes = r.get("cte_buckets_per_node") or []
        if not nodes:
            rb = r.get("result_buckets") or {}
            if rb:
                sigs.add(next(iter(rb.keys()))[:16])
            continue
        for b in nodes[0].get("buckets") or []:
            sig = b.get("result_signature_v2") or b.get("result_signature") or ""
            if sig:
                sigs.add(sig)
            cid = b.get("cluster_id")
            if cid is not None:
                sigs.add(str(cid))
    return len(sigs)


def reward_stats(rec: dict) -> Tuple[int, int, float]:
    rss = rec.get("rollout_stats") or []
    high = sum(1 for r in rss if float(r.get("reward", 0)) >= 0.99)
    return high, len(rss), max((float(r.get("reward", 0)) for r in rss), default=0.0)


def gold_partial_in_variants(rec: dict, gold_cols: Set[str]) -> bool:
    if not gold_cols:
        return False
    for r in rec.get("rollout_stats") or []:
        if float(r.get("reward", 0)) >= 0.8:
            continue
        for v in r.get("all_sql_variants") or []:
            sql = (v.get("sql") or "").lower()
            if any(c in sql for c in gold_cols):
                return True
    return False


def fast_baseline_recall(rec: dict) -> bool:
    return any(s.get("is_correct") for s in rec.get("all_sqls_with_attributes") or [])


def classify_one(
    rec: dict,
    gold_sql: str,
    ppl_row: Optional[dict],
) -> Tuple[str, str, dict]:
    gf = parse_sql_features(gold_sql)
    max_d = rollout_depths(rec)
    d1c = depth1_cluster_count(rec)
    high_r, n_roll, max_r = reward_stats(rec)
    ddl = (ppl_row or {}).get("ddl_data") or ""
    ddl_tables, ddl_cols = ddl_schema_from_text(ddl)
    ddl_chars = len(ddl)

    hits: List[str] = []
    meta = {
        "gold_cte": gf["cte_layers"],
        "max_rollout_depth": max_d,
        "d1_clusters": d1c,
        "high_reward_rollouts": high_r,
        "n_rollouts": n_roll,
        "ddl_chars": ddl_chars,
    }

    if gf["has_window"] or gf["has_recursive"] or gf["has_lateral"] or gf["cte_layers"] >= 4:
        hits.append("S6")
    missing_cols = gf["columns"] - ddl_cols if gf["columns"] else set()
    if missing_cols and ddl_chars > 3000:
        hits.append("S4")
    missing_tables = gf["tables"] - ddl_tables if gf["tables"] else set()
    if missing_tables and "S4" not in hits:
        hits.append("S3")
    if gf["cte_layers"] > max_d + 1:
        hits.append("S1")
    if d1c <= 2 and n_roll >= 6:
        hits.append("S2")
    if n_roll >= 6 and high_r >= 6 and max_r >= 0.99:
        hits.append("S7")
    if gold_partial_in_variants(rec, gf["columns"]):
        hits.append("S5")

    primary = "S0"
    secondary = ""
    for b in PRIORITY:
        if b in hits:
            primary = b
            rest = [x for x in hits if x != b]
            secondary = rest[0] if rest else ""
            break
    return primary, secondary, meta


def render_md(rows: List[dict], counts: Counter, baseline_hit_on_lost: int) -> str:
    n = len(rows)
    ts = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    lines = [
        "# Recall-lost 75 — 7-bucket taxonomy (R1)",
        "",
        f"Generated: {ts}",
        "",
        f"**Pool**: 498 merged (ef2 rerun overlay), oracle recall=False → **{n}** questions.",
        "",
        "## 1. 主表分布",
        "",
        "| Bucket | n | % | 修复方向 | 动 H1? | 动 30q? | paper? |",
        "|---|---:|---:|---|---|---|---|",
    ]
    for b in BUCKETS:
        c = counts.get(b, 0)
        fix, h1, q30, pap = FIX_PATH.get(b, ("—", "—", "—", "—"))
        lines.append(f"| **{b}** | {c} | {100*c/n:.1f}% | {fix} | {h1} | {q30} | {pap} |")

    lines += ["", "## 2. db_id / complexity", ""]
    for b in BUCKETS:
        sub = [r for r in rows if r["primary"] == b]
        if not sub:
            continue
        db_c = Counter(r["db_id"] for r in sub).most_common(3)
        cx_c = Counter(r["complexity"] for r in sub).most_common()
        lines.append(f"- **{b}** ({len(sub)}): db top3 {db_c}; complexity {dict(cx_c)}")

    s0 = [r["qid"] for r in rows if r["primary"] == "S0"]
    lines += [
        "",
        "## 3. S0 残差清单",
        "",
        f"`{sorted(s0, key=int)}`",
        "",
        "## 4. Baseline 交叉（Qwen r=20 legacy）",
        "",
        f"- recall-lost 中 baseline Hit@1: **{baseline_hit_on_lost}**",
        f"- baseline 有 recall 但我们仍 lost: **{sum(1 for r in rows if r['baseline_recall'])}**",
        f"- 双方均无 recall: **{sum(1 for r in rows if not r['baseline_recall'])}**",
        "",
        "## 5. 决策小结",
        "",
    ]
    top3 = counts.most_common(3)
    lines.append(
        f"- 主导桶: **{top3[0][0]}** ({top3[0][1]}), **{top3[1][0]}** ({top3[1][1]}), **{top3[2][0]}** ({top3[2][1]})"
    )
    s57 = counts.get("S5", 0) + counts.get("S7", 0)
    if s57 >= 30:
        lines.append(f"- ⚠️ **S5+S7 = {s57} (≥30)** → reward redesign 主路径")
    if counts.get("S6", 0) >= 30:
        lines.append("- ⚠️ **S6 ≥ 30** → model ceiling，paper 写 limitation")
    if counts.get("S0", 0) >= 15:
        lines.append(f"- 🛑 **S0 = {counts['S0']} (≥15)** → 补规则")
    lines.append("")
    return "\n".join(lines) + "\n"


def main() -> None:
    merged = load_merged()
    gold_sqls, qid_to_db = load_gold_meta()
    ppl = load_ppl_index()
    qids = sorted(merged.keys(), key=int)

    if not RECALL_CACHE_MERGED.exists():
        print("[R1] ERROR: missing recall_map_498_merged.json", flush=True)
        sys.exit(1)
    recall_raw = load_json(RECALL_CACHE_MERGED)
    recall = {q: bool(recall_raw.get(q, False)) for q in qids}
    lost = [q for q in qids if not recall.get(q, True)]
    print(f"[R1] recall-lost n={len(lost)} (expect 75)", flush=True)

    base = load_json(BASE_PATH)
    print("[R1] classify ...", flush=True)
    rows = []
    counts: Counter = Counter()
    for i, qid in enumerate(lost):
        rec = merged[qid]
        primary, secondary, meta = classify_one(rec, gold_sqls.get(qid, ""), ppl.get(qid))
        counts[primary] += 1
        rows.append(
            {
                "qid": qid,
                "db_id": qid_to_db.get(qid, ""),
                "complexity": gold_complexity(gold_sqls.get(qid, "")),
                "primary": primary,
                "secondary": secondary,
                "baseline_hit1": hit1(base.get(qid, {})),
                "baseline_recall": fast_baseline_recall(base.get(qid, {})),
                **meta,
            }
        )
        if (i + 1) % 25 == 0:
            print(f"  {i+1}/{len(lost)}", flush=True)

    baseline_hit_on_lost = sum(1 for r in rows if r["baseline_hit1"])
    md = render_md(rows, counts, baseline_hit_on_lost)
    (OUT_DIR / "recall_lost_75_taxonomy.md").write_text(md, encoding="utf-8")
    (OUT_DIR / "recall_lost_75_taxonomy.json").write_text(
        json.dumps({"n_lost": len(lost), "counts": dict(counts), "rows": rows}, indent=2),
        encoding="utf-8",
    )
    print(md, flush=True)
    print(f"[wrote] {OUT_DIR / 'recall_lost_75_taxonomy.md'}", flush=True)


if __name__ == "__main__":
    main()
