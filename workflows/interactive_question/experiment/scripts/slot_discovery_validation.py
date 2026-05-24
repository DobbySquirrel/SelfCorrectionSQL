#!/usr/bin/env python3
"""
Stage 1 slot discovery recall validation on BIRD-116 (schema-free, no parser).

Usage:
  python experiment/scripts/slot_discovery_validation.py
  python experiment/scripts/slot_discovery_validation.py --resume
  python experiment/scripts/slot_discovery_validation.py --limit 3
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from experiment.data.bird116_loader import load_bird116
from experiment.pipeline.llm_client import LLMClient, load_config
from experiment.scripts.axis_miss_regen_compare import axis_label, extract_predicates, load_jsonl

REGEN_COMPARE = ROOT / "experiment/results/regen_compare.json"
BIRD_CSV = ROOT / "dataset/ambiguity_116_with_evidence_sql_schema.csv"
RAW_OUT = ROOT / "experiment/results/slot_discovery_raw.jsonl"
JSON_OUT = ROOT / "experiment/results/slot_discovery_validation.json"
MD_OUT = ROOT / "experiment/results/slot_discovery_validation.md"

SLOT_DISCOVERY_PROMPT = """You are analyzing a natural language query against a database schema to identify potential semantic ambiguities.

Natural language question:
{nl_question}

Database schema (table.column list):
{schema_str}

Identify all underspecified semantic slots in this query along the following 5-axis taxonomy:

1. Reference Grounding: Table / Column / Join Path ambiguity
2. Value Grounding: Value Encoding / Format Normalization ambiguity
3. Measure Construction: Formula / Numeric / Boundary ambiguity
4. Ranking Target: Extremum / Method / Direction ambiguity
5. Output Control: Projection / Row Structure ambiguity

For each slot you identify, output a JSON object with:
{{
  "axis": "<one of the 5 categories + subcategory>",
  "description": "<short NL description of what is ambiguous>",
  "candidate_values": [
    "<value 1, e.g., a SQL fragment>",
    "<value 2, ...>",
    ...
  ]
}}

Output a JSON list of all identified slots. If a slot has no real ambiguity, do not include it. Each candidate_values list should contain 2-5 plausible options. Do NOT generate full SQL; only the relevant fragment for each slot.

Output format:
[
  {{"axis": "...", "description": "...", "candidate_values": ["...", ...]}},
  ...
]
"""

# LLM taxonomy axis -> atomic DSL axes (fixed mapping, one-to-many allowed)
LLM_AXIS_TO_ATOMIC: dict[str, list[str]] = {
    "reference: table": ["source:FROM"],
    "reference grounding: table": ["source:FROM"],
    "reference: column": ["aggregate:SELECT", "filter:WHERE", "aggregate:GROUP"],
    "reference grounding: column": ["aggregate:SELECT", "filter:WHERE", "aggregate:GROUP"],
    "reference: join path": ["combine:JOINS"],
    "reference grounding: join path": ["combine:JOINS"],
    "value grounding: encoding": ["filter:WHERE"],
    "value grounding: format normalization": ["filter:WHERE"],
    "value grounding: format norm": ["filter:WHERE"],
    "measure construction: formula": ["aggregate:SELECT"],
    "measure construction: numeric": ["aggregate:SELECT"],
    "measure construction: boundary": ["filter:WHERE", "combine:COMBINE_WHERE"],
    "ranking target: extremum": ["aggregate:ORDERBY", "aggregate:LIMIT"],
    "ranking target: method": ["aggregate:ORDERBY"],
    "ranking target: direction": ["aggregate:ORDERBY"],
    "output control: projection": ["aggregate:SELECT"],
    "output control: row structure": ["aggregate:DISTINCT", "aggregate:GROUP"],
}

SQL_KEYWORDS = frozenset({
    "select", "from", "where", "join", "inner", "left", "right", "on", "and", "or",
    "as", "by", "group", "order", "limit", "having", "distinct", "case", "when",
    "then", "else", "end", "not", "null", "count", "sum", "avg", "min", "max",
    "cast", "like", "between", "in", "is", "abs", "desc", "asc", "true", "false",
})


def normalize_axis_key(s: str) -> str:
    s = re.sub(r"\s+", " ", (s or "").lower().strip())
    s = s.replace("_", " ")
    return s


def map_llm_axis(llm_axis: str) -> list[str]:
    key = normalize_axis_key(llm_axis)
    if key in LLM_AXIS_TO_ATOMIC:
        return list(LLM_AXIS_TO_ATOMIC[key])

    axes: set[str] = set()

    def add(*items: str) -> None:
        axes.update(items)

    if "reference grounding" in key or key.startswith("reference"):
        if "join" in key:
            add("combine:JOINS")
        if "table" in key and "column" not in key and "join" not in key:
            add("source:FROM")
        if "column" in key:
            add("aggregate:SELECT", "filter:WHERE", "aggregate:GROUP")
        if "table / column / join" in key or (
            "table" in key and "column" in key and "join" in key
        ):
            add("source:FROM", "combine:JOINS", "aggregate:SELECT", "filter:WHERE", "aggregate:GROUP")
        if not axes and "reference" in key:
            add("source:FROM", "aggregate:SELECT", "combine:JOINS")

    if "value grounding" in key or "value encoding" in key or "format" in key:
        add("filter:WHERE")

    if "measure construction" in key or "measure" in key:
        if "boundary" in key:
            add("filter:WHERE", "combine:COMBINE_WHERE")
        elif "numeric" in key:
            add("aggregate:SELECT")
        elif "formula" in key or "measure" in key:
            add("aggregate:SELECT")

    if "ranking target" in key or "ranking" in key:
        if "extremum" in key:
            add("aggregate:ORDERBY", "aggregate:LIMIT")
        else:
            add("aggregate:ORDERBY")

    if "output control" in key or "output" in key:
        if "row structure" in key or "row" in key:
            add("aggregate:DISTINCT", "aggregate:GROUP")
        if "projection" in key or not ("row" in key):
            add("aggregate:SELECT")

    if not axes:
        for pat, mapped in LLM_AXIS_TO_ATOMIC.items():
            if pat in key or key in pat:
                axes.update(mapped)

    return sorted(axes)


def normalize_value(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").lower().strip())


def extract_tokens(value: str) -> set[str]:
    cleaned = re.sub(r"[^\w\s.\"]", " ", value.lower())
    tokens: set[str] = set()
    for tok in re.findall(r'[\w"]+', cleaned):
        t = tok.strip('"').strip("'")
        if len(t) <= 2 or t in SQL_KEYWORDS or t.isdigit():
            continue
        tokens.add(t)
        if "." in t:
            for part in t.split("."):
                if len(part) > 2 and part not in SQL_KEYWORDS:
                    tokens.add(part)
    return tokens


def value_match_tier(gold_value: str, candidates: list[str]) -> str:
    """Return exact | semantic | miss."""
    gv = normalize_value(gold_value)
    if not gv:
        return "miss"
    tokens = extract_tokens(gold_value)
    for c in candidates:
        cv = normalize_value(c)
        if not cv:
            continue
        if gv == cv:
            return "exact"
        if gv in cv or cv in gv:
            return "semantic"
        if tokens:
            hits = sum(1 for t in tokens if t in cv)
            if hits >= max(1, min(2, len(tokens))):
                return "semantic"
        if gold_value.startswith("combine:JOINS") or "{" in gold_value:
            try:
                obj = json.loads(gold_value)
                for k in ("target", "on", "type"):
                    v = str(obj.get(k, "")).lower()
                    if v and v in cv:
                        return "semantic"
            except json.JSONDecodeError:
                pass
    return "miss"


def parse_json_list(text: str) -> list | None:
    text = text.strip()
    m = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if m:
        text = m.group(1).strip()
    try:
        data = json.loads(text)
        if isinstance(data, list):
            return data
    except json.JSONDecodeError:
        pass
    start, end = text.find("["), text.rfind("]")
    if start >= 0 and end > start:
        try:
            data = json.loads(text[start : end + 1])
            if isinstance(data, list):
                return data
        except json.JSONDecodeError:
            pass
    return None


def select_validation_qids(n: int = 30) -> list[str]:
    regen = json.loads(REGEN_COMPARE.read_text())
    step_a = regen["step_a_summary"]["per_case_details"]
    r25 = [c["qid"] for c in regen["per_case"]]
    sel: list[str] = list(r25)
    rows = sorted(
        step_a,
        key=lambda c: (-c.get("n_worlds", 0), -c.get("num_axis_miss", 0), int(c["qid"])),
    )
    for c in rows:
        if c["qid"] not in sel:
            sel.append(c["qid"])
        if len(sel) >= n:
            break
    return sel[:n]


def load_raw_log(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    out: dict[str, dict] = {}
    for line in path.read_text().splitlines():
        if line.strip():
            rec = json.loads(line)
            out[str(rec["qid"])] = rec
    return out


def append_raw(path: Path, rec: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def slots_to_atomic_map(slots: list[dict]) -> tuple[dict[str, list[str]], list[str]]:
    """atomic_axis -> merged candidate values from all mapped LLM slots."""
    out: dict[str, list[str]] = {}
    unmapped: list[str] = []
    for slot in slots:
        if not isinstance(slot, dict):
            continue
        llm_axis = str(slot.get("axis", ""))
        cands = slot.get("candidate_values") or []
        if not isinstance(cands, list):
            cands = []
        mapped = map_llm_axis(llm_axis)
        if not mapped:
            unmapped.append(llm_axis)
            continue
        for ax in mapped:
            out.setdefault(ax, [])
            for v in cands:
                out[ax].append(str(v))
    return out, unmapped


def analyze_case(qid: str, gold_axes: dict[str, str], slots: list[dict]) -> dict[str, Any]:
    atomic_cands, unmapped_axes = slots_to_atomic_map(slots)
    gold_set = set(gold_axes.keys())
    pred_set = set(atomic_cands.keys())

    hit_axes = gold_set & pred_set
    axis_recall = len(hit_axes) / len(gold_set) if gold_set else 0.0

    value_tiers: dict[str, str] = {}
    for ax in sorted(hit_axes):
        value_tiers[ax] = value_match_tier(gold_axes[ax], atomic_cands.get(ax, []))

    n_hit = len(hit_axes)
    exact_n = sum(1 for t in value_tiers.values() if t == "exact")
    semantic_n = sum(1 for t in value_tiers.values() if t == "semantic")
    miss_n = sum(1 for t in value_tiers.values() if t == "miss")

    fp_axes = pred_set - gold_set
    fp_rate = len(fp_axes) / len(pred_set) if pred_set else 0.0

    slot_value_counts = [
        len(s.get("candidate_values") or [])
        for s in slots
        if isinstance(s, dict)
    ]
    avg_val = sum(slot_value_counts) / len(slot_value_counts) if slot_value_counts else 0.0

    return {
        "qid": qid,
        "gold_axis_count": len(gold_set),
        "predicted_axis_count": len(pred_set),
        "axis_recall": round(axis_recall, 4),
        "value_recall_exact": round(exact_n / n_hit, 4) if n_hit else None,
        "value_recall_semantic": round((exact_n + semantic_n) / n_hit, 4) if n_hit else None,
        "value_exact_count": exact_n,
        "value_semantic_count": semantic_n,
        "value_miss_count": miss_n,
        "hit_axis_count": n_hit,
        "false_positive_rate": round(fp_rate, 4),
        "false_positive_axes": sorted(fp_axes),
        "false_positive_count": len(fp_axes),
        "avg_value_count": round(avg_val, 2),
        "slot_count": len(slots),
        "unmapped_llm_axes": unmapped_axes,
        "gold_axes": gold_axes,
        "predicted_atomic_candidates": atomic_cands,
        "value_tiers": value_tiers,
        "predicted_slots": slots,
    }


def aggregate(results: list[dict]) -> dict[str, Any]:
    n = len(results)
    axis_recalls = [r["axis_recall"] for r in results]
    avg_axis = sum(axis_recalls) / n if n else 0.0

    val_cases = [r for r in results if r["hit_axis_count"] > 0]
    avg_val_sem = (
        sum(r["value_recall_semantic"] or 0 for r in val_cases) / len(val_cases)
        if val_cases else 0.0
    )
    avg_val_exact = (
        sum(r["value_recall_exact"] or 0 for r in val_cases) / len(val_cases)
        if val_cases else 0.0
    )

    total_hit = sum(r["hit_axis_count"] for r in results)
    total_exact = sum(r["value_exact_count"] for r in results)
    total_sem = sum(r["value_semantic_count"] for r in results)
    total_miss = sum(r["value_miss_count"] for r in results)

    fp_rates = [r["false_positive_rate"] for r in results if r["predicted_axis_count"] > 0]
    avg_fp = sum(fp_rates) / len(fp_rates) if fp_rates else 0.0
    total_pred_axes = sum(r["predicted_axis_count"] for r in results)
    total_fp_axes = sum(r["false_positive_count"] for r in results)

    all_val_counts: list[int] = []
    for r in results:
        for slot in r.get("predicted_slots") or []:
            if isinstance(slot, dict):
                all_val_counts.append(len(slot.get("candidate_values") or []))

    if avg_axis >= 0.8 and avg_val_sem >= 0.7:
        verdict = "Stage 1 在 BIRD-116 上 viable, 推荐进入完整 v0.7 pipeline 实现"
        viability = "strong"
    elif avg_axis < 0.6 or avg_val_sem < 0.5:
        verdict = (
            "Stage 1 在 schema-free 下不 viable, slot-first 范式需要重新评估 "
            "(e.g., 加入 candidate SQL 作为 hint)"
        )
        viability = "low"
    else:
        verdict = "Stage 1 prompt 需要工程改进, 但 framework 可行, 建议先做 prompt iteration"
        viability = "mid"

    q1 = "yes" if avg_axis >= 0.8 else ("partial" if avg_axis >= 0.6 else "no")
    q2 = "yes" if avg_val_sem >= 0.7 else ("partial" if avg_val_sem >= 0.5 else "no")
    q3 = "yes" if avg_fp <= 0.3 else ("partial" if avg_fp <= 0.5 else "no")
    q4 = "yes" if all_val_counts else "no"

    return {
        "n_cases": n,
        "avg_axis_recall": round(avg_axis, 4),
        "axis_recall_100pct_cases": sum(1 for x in axis_recalls if x >= 0.999),
        "axis_recall_lt_half_cases": sum(1 for x in axis_recalls if x < 0.5),
        "avg_value_recall_semantic": round(avg_val_sem, 4),
        "avg_value_recall_exact": round(avg_val_exact, 4),
        "micro_value_exact_rate": round(total_exact / total_hit, 4) if total_hit else 0.0,
        "micro_value_semantic_rate": round((total_exact + total_sem) / total_hit, 4) if total_hit else 0.0,
        "micro_value_miss_rate": round(total_miss / total_hit, 4) if total_hit else 0.0,
        "avg_false_positive_rate": round(avg_fp, 4),
        "total_predicted_atomic_axes": total_pred_axes,
        "total_false_positive_axes": total_fp_axes,
        "avg_slot_value_count": round(statistics.mean(all_val_counts), 2) if all_val_counts else 0.0,
        "median_slot_value_count": statistics.median(all_val_counts) if all_val_counts else 0,
        "max_slot_value_count": max(all_val_counts) if all_val_counts else 0,
        "min_slot_value_count": min(all_val_counts) if all_val_counts else 0,
        "verdict": verdict,
        "viability": viability,
        "judgment": {"Q1_axis_coverage": q1, "Q2_value_recall": q2, "Q3_false_positive": q3, "Q4_value_list_size": q4},
    }


def pick_trace_qids(results: list[dict]) -> list[str]:
    by_recall = sorted(results, key=lambda r: r["axis_recall"], reverse=True)
    high = [r["qid"] for r in by_recall if r["axis_recall"] >= 0.8][:2]
    low = [r["qid"] for r in reversed(by_recall) if r["axis_recall"] < 0.5][:1]
    mid = [
        r["qid"] for r in by_recall
        if 0.5 <= r["axis_recall"] < 0.8
    ][:2]
    trace: list[str] = []
    seen: set[str] = set()
    for q in high + mid + low:
        if q not in seen:
            trace.append(q)
            seen.add(q)
    for r in by_recall:
        if len(trace) >= 5:
            break
        if r["qid"] not in seen:
            trace.append(r["qid"])
            seen.add(r["qid"])
    return trace[:5]


def render_markdown(results: list[dict], agg: dict[str, Any], trace_qids: list[str], qids: list[str]) -> str:
    lines: list[str] = []
    w = lines.append
    w("# Stage 1 Slot Discovery Validation (BIRD-116)\n\n")
    w(f"Validation subset: **{len(qids)} cases** (E4 Step-B 25 + top open-world by |W|/axis-miss).\n\n")

    w("## Q1–Q4 判断\n\n")
    w("| Q | 判断 | Evidence |\n|---|---|---|\n")
    j = agg["judgment"]
    w(f"| Q1 axis coverage | **{j['Q1_axis_coverage']}** | avg axis recall = {agg['avg_axis_recall']:.4f}; "
      f"100% recall = {agg['axis_recall_100pct_cases']}/{agg['n_cases']}; "
      f"<50% = {agg['axis_recall_lt_half_cases']}/{agg['n_cases']} |\n")
    w(f"| Q2 value recall | **{j['Q2_value_recall']}** | avg semantic recall (hit axes) = {agg['avg_value_recall_semantic']:.4f}; "
      f"exact = {agg['avg_value_recall_exact']:.4f}; micro miss = {agg['micro_value_miss_rate']:.4f} |\n")
    w(f"| Q3 false-positive | **{j['Q3_false_positive']}** | avg FP rate = {agg['avg_false_positive_rate']:.4f}; "
      f"FP axes = {agg['total_false_positive_axes']}/{agg['total_predicted_atomic_axes']} |\n")
    w(f"| Q4 value list size | **{j['Q4_value_list_size']}** | mean={agg['avg_slot_value_count']}, "
      f"median={agg['median_slot_value_count']}, min={agg['min_slot_value_count']}, max={agg['max_slot_value_count']} |\n")

    w("\n## Per-case 表\n\n")
    w("| qid | gold_axis_count | predicted_axis_count | axis_recall | value_recall (exact) | "
      "value_recall (semantic) | false_positive_rate | avg_value_count |\n")
    w("|---|---|---|---|---|---|---|---|\n")
    for r in results:
        ve = r["value_recall_exact"]
        vs = r["value_recall_semantic"]
        w(
            f"| {r['qid']} | {r['gold_axis_count']} | {r['predicted_axis_count']} | {r['axis_recall']:.3f} | "
            f"{ve if ve is not None else '—'} | {vs if vs is not None else '—'} | "
            f"{r['false_positive_rate']:.3f} | {r['avg_value_count']} |\n"
        )

    w("\n## 5 case trace\n\n")
    by_qid = {r["qid"]: r for r in results}
    examples_meta = {r["qid"]: r.get("nl_question", "") for r in results}
    for qid in trace_qids:
        r = by_qid[qid]
        w(f"### qid={qid}\n\n")
        nl = r.get("nl_question") or examples_meta.get(qid, "")
        w(f"**NL:** {nl[:280]}{'...' if len(nl) > 280 else ''}\n\n")
        w("**Gold atomic axes:**\n\n")
        for ax, val in sorted(r["gold_axes"].items()):
            vp = val if len(val) <= 70 else val[:67] + "..."
            w(f"- `{ax}`: `{vp}`\n")
        w("\n**LLM slots → mapped atomic candidates:**\n\n")
        for slot in r.get("predicted_slots") or []:
            llm_ax = slot.get("axis", "?")
            mapped = map_llm_axis(str(llm_ax))
            w(f"- LLM `{llm_ax}` → {mapped or ['UNMAPPED']}\n")
            for v in (slot.get("candidate_values") or [])[:4]:
                w(f"  - `{str(v)[:70]}`\n")
        w("\n**Hit/Miss/FP:**\n\n")
        for ax in sorted(r["gold_axes"].keys()):
            if ax in r.get("value_tiers", {}):
                w(f"- HIT `{ax}`: tier={r['value_tiers'][ax]}\n")
            elif ax in set(r["gold_axes"]) - set(r.get("value_tiers", {})):
                w(f"- MISS axis (not predicted): `{ax}`\n")
        for ax in r.get("false_positive_axes") or []:
            w(f"- FP atomic axis: `{ax}`\n")
        w(f"\n**一句话:** axis_recall={r['axis_recall']:.2f}, "
          f"value_semantic={r['value_recall_semantic']}, FP_rate={r['false_positive_rate']:.2f}.\n\n")

    w("\n## Step 7 总判断\n\n")
    w(f"**{agg['verdict']}** ({agg['viability']})\n")
    return "".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--resume", action="store_true")
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args()

    qids = select_validation_qids(30)
    if args.limit:
        qids = qids[: args.limit]

    cfg = load_config()
    examples = {ex.qid: ex for ex in load_bird116(BIRD_CSV, cfg["data"]["bird_db_root"])}
    llm = LLMClient(preset="yi_zhan_gpt-4o")

    raw_cache = load_raw_log(RAW_OUT) if args.resume else {}
    results: list[dict] = []
    failures = 0
    unmapped_total = 0

    for qid in qids:
        ex = examples[qid]
        gold_axes = {axis_label(k): v for k, v in extract_predicates(ex.gold_sql, ex.db_path).items()}

        if qid in raw_cache and raw_cache[qid].get("status") == "ok":
            slots = raw_cache[qid]["predicted_slots"]
            nl_q = raw_cache[qid].get("nl_question", ex.question_raw or ex.question)
        else:
            nl_q = ex.question_raw or ex.question
            schema = ex.schema_no_content or ex.schema
            prompt = SLOT_DISCOVERY_PROMPT.format(nl_question=nl_q, schema_str=schema)
            resp = llm.complete(
                [{"role": "user", "content": prompt}],
                temperature=0.0,
                max_tokens=4096,
            )
            if resp.finish_reason and str(resp.finish_reason).startswith("error:"):
                failures += 1
                rec = {"qid": qid, "status": "error", "error": resp.finish_reason, "raw": resp.text}
                append_raw(RAW_OUT, rec)
                print(f"ERROR qid={qid}: {resp.finish_reason}", file=sys.stderr)
                if failures >= 3:
                    sys.exit(1)
                continue
            slots = parse_json_list(resp.text)
            if slots is None:
                failures += 1
                rec = {"qid": qid, "status": "error", "error": "parse_failed", "raw": resp.text}
                append_raw(RAW_OUT, rec)
                if failures >= 3:
                    sys.exit(1)
                continue
            rec = {
                "qid": qid,
                "status": "ok",
                "nl_question": nl_q,
                "db_id": ex.db_id,
                "predicted_slots": slots,
                "raw": resp.text,
            }
            append_raw(RAW_OUT, rec)
            raw_cache[qid] = rec

        row = analyze_case(qid, gold_axes, slots)
        row["nl_question"] = nl_q
        row["n_worlds_hint"] = None
        unmapped_total += len(row.get("unmapped_llm_axes") or [])
        results.append(row)
        print(
            f"qid={qid} axis_recall={row['axis_recall']:.3f} "
            f"val_sem={row['value_recall_semantic']} fp={row['false_positive_rate']:.3f}"
        )

    if unmapped_total > len(qids) * 2:
        print(
            f"WARNING: high unmapped LLM axis count ({unmapped_total}); check mapping rules",
            file=sys.stderr,
        )

    agg = aggregate(results)
    trace_qids = pick_trace_qids(results)
    md = render_markdown(results, agg, trace_qids, qids)

    JSON_OUT.parent.mkdir(parents=True, exist_ok=True)
    JSON_OUT.write_text(
        json.dumps({"qids": qids, "per_case": results, "aggregate": agg, "trace_qids": trace_qids}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    MD_OUT.write_text(md, encoding="utf-8")
    print(md)
    print(f"\nWrote {MD_OUT}\nWrote {JSON_OUT}\nRaw: {RAW_OUT}")


if __name__ == "__main__":
    main()
