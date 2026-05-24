#!/usr/bin/env python3
"""
Sanity check: Slot-First Clarification Pipeline (Path 1+3) vs zero-shot baseline.

Usage:
  python experiment/scripts/sanity_check_path13.py
  python experiment/scripts/sanity_check_path13.py --limit 3
  python experiment/scripts/sanity_check_path13.py --resume
"""

from __future__ import annotations

import argparse
import itertools
import json
import math
import re
import sqlite3
import statistics
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from experiment.data.bird116_loader import load_bird116
from experiment.pipeline.executor import execute_sql
from experiment.pipeline.llm_client import LLMClient, load_config
from experiment.pipeline.selectors import OptionSpec, Question, compute_eig
from experiment.pipeline.items import InteractionItem
from experiment.scripts.axis_miss_regen_compare import axis_label, extract_predicates, mcnemar_p_value
from experiment.scripts import slot_discovery_validation as sdv

STAGE1_RAW = ROOT / "experiment/results/slot_discovery_raw.jsonl"
BIRD_CSV = ROOT / "dataset/ambiguity_116_with_evidence_sql_schema.csv"
JSON_OUT = ROOT / "experiment/results/sanity_check_path13.json"
MD_OUT = ROOT / "experiment/results/sanity_check_path13_report.md"
PROMPTS = {
    "baseline": ROOT / "prompts/baseline_text2sql.md",
    "stage2": ROOT / "prompts/stage2_axisvalue_to_sql.md",
    "stage4": ROOT / "prompts/stage4_text2sql_constrained.md",
}

W_RAW_CAP = 12
MAX_ROUNDS = 5
EXEC_TIMEOUT = 10.0
EXPLAIN_TIMEOUT = 5.0


@dataclass
class SlotDim:
    slot_id: str
    axis_name: str
    candidates: list[str]


@dataclass
class World:
    wid: str
    axis_values: dict[str, str]
    sql: str = ""
    exec_hash: str | None = None
    weight: float = 1.0

    def slot_axis_map(self, slots: list[SlotDim]) -> dict[str, str]:
        return {
            slots[i].axis_name if i < len(slots) else k: v
            for i, (k, v) in enumerate(self.axis_values.items())
        }


def load_stage1() -> dict[str, dict]:
    out: dict[str, dict] = {}
    for line in STAGE1_RAW.read_text().splitlines():
        if line.strip():
            rec = json.loads(line)
            if rec.get("status") == "ok":
                out[str(rec["qid"])] = rec
    return out


def load_prompt(name: str) -> str:
    return PROMPTS[name].read_text(encoding="utf-8")


def render(template: str, **kwargs: str) -> str:
    out = template
    for k, v in kwargs.items():
        out = out.replace("{" + k + "}", v)
    return out


def extract_sql(text: str) -> str:
    text = (text or "").strip()
    m = re.search(r"```(?:sql)?\s*([\s\S]*?)```", text, re.I)
    if m:
        return m.group(1).strip()
    lines = [ln for ln in text.splitlines() if not ln.strip().startswith("--")]
    return "\n".join(lines).strip()


def slots_from_stage1(predicted_slots: list[dict]) -> list[SlotDim]:
    dims: list[SlotDim] = []
    for i, slot in enumerate(predicted_slots):
        if not isinstance(slot, dict):
            continue
        axis = str(slot.get("axis", f"axis_{i}"))
        cands = slot.get("candidate_values") or []
        if not isinstance(cands, list):
            cands = []
        cands = [str(c) for c in cands if str(c).strip()]
        if not cands:
            continue
        dims.append(SlotDim(slot_id=f"slot_{i}", axis_name=axis, candidates=cands))
    return dims


def cartesian_lex_cap(slots: list[SlotDim], cap: int = W_RAW_CAP) -> list[dict[str, str]]:
    if not slots:
        return []
    keys = [s.slot_id for s in slots]
    lists = [s.candidates for s in slots]
    combos = [dict(zip(keys, vals)) for vals in itertools.product(*lists)]
    combos.sort(key=lambda d: tuple(d[k] for k in keys))
    return combos[:cap]


def explain_ok(sql: str, db_path: str) -> bool:
    if not sql.strip():
        return False
    conn = sqlite3.connect(db_path)
    try:
        t0 = time.time()
        cur = conn.cursor()
        cur.execute(f"EXPLAIN {sql}")
        cur.fetchall()
        return (time.time() - t0) <= EXPLAIN_TIMEOUT
    except Exception:
        return False
    finally:
        conn.close()


def exec_hash(sql: str, db_path: str) -> tuple[bool, str | None]:
    r = execute_sql(db_path, sql, timeout_s=EXEC_TIMEOUT)
    if r.ok and r.result_hash:
        return True, r.result_hash
    return False, None


def ex_correct(sql: str, db_path: str, gold_hash: str | None) -> tuple[int, str | None]:
    if not gold_hash:
        return 0, None
    ok, h = exec_hash(sql, db_path)
    if ok and h == gold_hash:
        return 1, h
    return 0, h


def llm_sql(llm: LLMClient, prompt: str) -> str:
    resp = llm.complete([{"role": "user", "content": prompt}], temperature=0.0, max_tokens=4096)
    if resp.finish_reason and str(resp.finish_reason).startswith("error:"):
        return ""
    return extract_sql(resp.text or "")


def format_axis_values(axis_values: dict[str, str], slots: list[SlotDim]) -> str:
    id_to_axis = {s.slot_id: s.axis_name for s in slots}
    lines = []
    for sid in sorted(axis_values.keys()):
        axis = id_to_axis.get(sid, sid)
        lines.append(f"- {axis}: {axis_values[sid]}")
    return "\n".join(lines)


def materialize_worlds(
    combos: list[dict[str, str]],
    slots: list[SlotDim],
    schema_str: str,
    db_path: str,
    llm: LLMClient,
) -> list[World]:
    worlds: list[World] = []
    for i, av in enumerate(combos):
        prompt = render(
            load_prompt("stage2"),
            schema_str=schema_str,
            axis_values_str=format_axis_values(av, slots),
        )
        sql = llm_sql(llm, prompt)
        worlds.append(World(wid=f"w{i}", axis_values=av, sql=sql))
    return worlds


def filter_worlds(
    worlds: list[World],
    db_path: str,
) -> tuple[list[World], dict[str, int]]:
    stats = {"explain_fail": 0, "exec_fail": 0, "passed": 0}
    passed: list[World] = []
    for w in worlds:
        if not w.sql or not explain_ok(w.sql, db_path):
            stats["explain_fail"] += 1
            continue
        ok, h = exec_hash(w.sql, db_path)
        if not ok:
            stats["exec_fail"] += 1
            continue
        w.exec_hash = h
        stats["passed"] += 1
        passed.append(w)
    # collapse by exec_hash, keep first representative (no dedup of axis_values per task - but same hash collapse)
    seen: dict[str, World] = {}
    collapsed: list[World] = []
    for w in passed:
        assert w.exec_hash
        if w.exec_hash not in seen:
            seen[w.exec_hash] = w
            collapsed.append(w)
    return collapsed, stats


def worlds_to_items(worlds: list[World]) -> list[InteractionItem]:
    return [
        InteractionItem(
            key=w.wid,
            weight=w.weight,
            exec_hash=w.exec_hash or w.wid,
            representative_sql=w.sql,
        )
        for w in worlds
    ]


def binary_question_for(slot_id: str, value: str, worlds: list[World]) -> Question:
    yes_keys = {w.wid for w in worlds if w.axis_values.get(slot_id) == value}
    all_keys = {w.wid for w in worlds}
    no_keys = all_keys - yes_keys
    return Question(
        options=[
            OptionSpec(label=f"yes:{slot_id}={value[:40]}", world_hashes=yes_keys, item_keys=yes_keys),
            OptionSpec(label="no", world_hashes=no_keys, item_keys=no_keys),
        ],
        source=f"binary:{slot_id}",
        metadata={"slot_id": slot_id, "value": value},
    )


def gold_value_for_slot(slot: SlotDim, gold_axes: dict[str, str]) -> str | None:
    mapped = sdv.map_llm_axis(slot.axis_name)
    gold_vals = [gold_axes[ax] for ax in mapped if ax in gold_axes]
    if not gold_vals:
        return None
    for g in gold_vals:
        for c in slot.candidates:
            if sdv.value_match_tier(g, [c]) != "miss":
                return c
    return gold_vals[0]


def values_match(a: str, b: str) -> bool:
    return sdv.normalize_value(a) == sdv.normalize_value(b) or sdv.value_match_tier(a, [b]) != "miss"


def oracle_answer(
    slot: SlotDim,
    v_star: str,
    gold_axes: dict[str, str],
) -> tuple[str, str | None]:
    """Return ('yes'|'no'|'miss', v_gold_candidate_or_none)."""
    v_gold = gold_value_for_slot(slot, gold_axes)
    if v_gold is None:
        return "miss", None
    if values_match(v_gold, v_star):
        return "yes", v_gold
    for c in slot.candidates:
        if c != v_star and values_match(v_gold, c):
            return "no", v_gold
    return "miss", v_gold


REGEN_PROMPT = """Given NL question, schema, and a missed ambiguity slot, suggest 2-4 additional candidate values for this slot only.
Use values plausible for the schema. Output JSON array of strings only.

NL: {nl_question}
Schema: {schema_str}
Slot axis: {axis_name}
Existing candidates: {existing}
Known resolved constraints:
{constraints}
"""


def regenerate_slot_values(
    llm: LLMClient,
    slot: SlotDim,
    nl: str,
    schema: str,
    constraints: list[tuple[str, str]],
) -> list[str]:
    cstr = "\n".join(f"- {a}: {v}" for a, v in constraints) or "(none)"
    prompt = REGEN_PROMPT.format(
        nl_question=nl,
        schema_str=schema[:3000],
        axis_name=slot.axis_name,
        existing=json.dumps(slot.candidates, ensure_ascii=False),
        constraints=cstr,
    )
    resp = llm.complete([{"role": "user", "content": prompt}], temperature=0.0, max_tokens=1024)
    text = resp.text or ""
    m = re.search(r"\[[\s\S]*\]", text)
    if not m:
        return []
    try:
        arr = json.loads(m.group(0))
        if isinstance(arr, list):
            return [str(x) for x in arr if str(x).strip()]
    except json.JSONDecodeError:
        pass
    return []


def run_stage3(
    worlds: list[World],
    slots: list[SlotDim],
    gold_axes: dict[str, str],
    nl: str,
    schema: str,
    db_path: str,
    llm: LLMClient,
) -> tuple[list[World], list[dict], list[tuple[str, str]], int, bool, bool]:
    """Returns W, qa_trace, constraints, rounds, regen_triggered, fail_resolve."""
    W = list(worlds)
    asked: set[str] = set()
    constraints: list[tuple[str, str]] = []
    qa_trace: list[dict] = []
    regen = False
    fail_resolve = False

    for round_i in range(MAX_ROUNDS):
        if len(W) <= 1:
            break
        remaining = [s for s in slots if s.slot_id not in asked]
        if not remaining:
            break

        best_eig = -1.0
        best_slot: SlotDim | None = None
        best_val: str | None = None
        items = worlds_to_items(W)

        for slot in remaining:
            vals = sorted({w.axis_values.get(slot.slot_id, "") for w in W if slot.slot_id in w.axis_values})
            for v in vals:
                if not v:
                    continue
                q = binary_question_for(slot.slot_id, v, W)
                eig = compute_eig(q, items)
                if eig > best_eig:
                    best_eig = eig
                    best_slot = slot
                    best_val = v

        if best_slot is None or best_val is None:
            break

        ans, v_gold = oracle_answer(best_slot, best_val, gold_axes)
        trace = {
            "round": round_i + 1,
            "slot_id": best_slot.slot_id,
            "axis": best_slot.axis_name,
            "asked_value": best_val,
            "eig": round(best_eig, 4),
            "oracle": ans,
            "v_gold": v_gold,
        }

        if ans == "miss":
            if not regen:
                regen = True
                new_vals = regenerate_slot_values(llm, best_slot, nl, schema, constraints)
                added = [v for v in new_vals if v not in best_slot.candidates]
                if added:
                    best_slot.candidates.extend(added)
                    # materialize only new combos for this slot (minimal: add worlds with new values)
                    new_combos = []
                    for av in cartesian_lex_cap(slots, cap=9999):
                        if av.get(best_slot.slot_id) in added:
                            new_combos.append(av)
                    new_combos = new_combos[:W_RAW_CAP]
                    new_worlds = materialize_worlds(new_combos[: min(4, len(new_combos))], slots, schema, db_path, llm)
                    nf, _ = filter_worlds(new_worlds, db_path)
                    W.extend(nf)
                    W = list({w.exec_hash: w for w in W if w.exec_hash}.values()) if W else []
                    ans2, v_gold2 = oracle_answer(best_slot, best_val, gold_axes)
                    trace["regen_added"] = added
                    trace["regen_retry_oracle"] = ans2
                    if ans2 == "yes":
                        ans, v_gold = ans2, v_gold2
                    elif ans2 == "no":
                        ans, v_gold = ans2, v_gold2
                    else:
                        fail_resolve = True
                        trace["fail_resolve_axis"] = best_slot.axis_name
                        qa_trace.append(trace)
                        asked.add(best_slot.slot_id)
                        continue
                else:
                    fail_resolve = True
                    trace["fail_resolve_axis"] = best_slot.axis_name
                    qa_trace.append(trace)
                    asked.add(best_slot.slot_id)
                    continue
            else:
                fail_resolve = True
                trace["fail_resolve_axis"] = best_slot.axis_name
                qa_trace.append(trace)
                asked.add(best_slot.slot_id)
                continue

        if ans == "yes":
            W = [w for w in W if w.axis_values.get(best_slot.slot_id) == best_val]
            constraints.append((best_slot.axis_name, v_gold or best_val))
        else:
            W = [w for w in W if w.axis_values.get(best_slot.slot_id) != best_val]

        trace["w_remaining"] = len(W)
        qa_trace.append(trace)
        asked.add(best_slot.slot_id)

    return W, qa_trace, constraints, len(qa_trace), regen, fail_resolve


def stage1_miss_axis(gold_axes: dict[str, str], slots: list[SlotDim]) -> bool:
    pred_atomic: set[str] = set()
    for s in slots:
        pred_atomic.update(sdv.map_llm_axis(s.axis_name))
    gold_set = set(gold_axes.keys())
    return len(gold_set - pred_atomic) > 0


def classify_bucket(
    *,
    baseline_ex: int,
    pipeline_ex: int,
    stage1_miss: bool,
    w_filtered: int,
    w_final: int,
    fail_resolve: bool,
    constraints: list,
) -> str:
    if stage1_miss:
        return "stage1_miss_axis"
    if w_filtered == 0:
        return "stage2_no_valid_sql"
    if w_final > 1 or fail_resolve:
        return "stage3_under_determined"
    if baseline_ex and pipeline_ex:
        return "pipeline_ok_baseline_ok"
    if not baseline_ex and pipeline_ex:
        return "pipeline_ok_baseline_fail"
    if baseline_ex and not pipeline_ex:
        return "pipeline_fail_baseline_ok"
    if constraints and not pipeline_ex:
        return "stage4_fail"
    return "stage4_fail"


def mcnemar_two_sided(b: int, c: int) -> float | None:
    """b=baseline ok pipeline fail, c=baseline fail pipeline ok."""
    n = b + c
    if n == 0:
        return None
    try:
        from scipy.stats import binomtest
        k = min(b, c)
        return float(binomtest(k, n=n, p=0.5, alternative="two-sided").pvalue)
    except Exception:
        if n == 0:
            return None
        chi2 = (abs(b - c) - 1) ** 2 / n if n else 0
        # approx p from chi2 1df
        return math.erfc(math.sqrt(chi2 / 2))


def run_case(qid: str, stage1_rec: dict, example: Any, llm: LLMClient) -> dict[str, Any]:
    nl = stage1_rec.get("nl_question") or example.question_raw or example.question
    schema = example.schema or example.schema_no_content or ""
    slots = slots_from_stage1(stage1_rec["predicted_slots"])
    gold_axes = {axis_label(k): v for k, v in extract_predicates(example.gold_sql, example.db_path).items()}

    _, gold_hash = exec_hash(example.gold_sql, example.db_path)
    s1_miss = stage1_miss_axis(gold_axes, slots)

    # Stage 0
    p0 = render(load_prompt("baseline"), schema_str=schema, nl_question=nl)
    baseline_sql = llm_sql(llm, p0)
    baseline_ex, baseline_h = ex_correct(baseline_sql, example.db_path, gold_hash)

    # Stage 2
    combos = cartesian_lex_cap(slots, W_RAW_CAP)
    raw_worlds = materialize_worlds(combos, slots, schema, example.db_path, llm)
    w_filtered, drop_stats = filter_worlds(raw_worlds, example.db_path)

    # Stage 3
    w_final, qa_trace, constraints, rounds, regen, fail_resolve = run_stage3(
        w_filtered, slots, gold_axes, nl, schema, example.db_path, llm,
    )

    # Stage 4
    cstr = "\n".join(f"- {a}: {v}" for a, v in constraints) or "(none — use best judgment from resolved slots if empty)"
    p4 = render(load_prompt("stage4"), schema_str=schema, nl_question=nl, constraints_str=cstr)
    pipeline_sql = llm_sql(llm, p4)
    pipeline_ex, pipeline_h = ex_correct(pipeline_sql, example.db_path, gold_hash)

    bucket = classify_bucket(
        baseline_ex=baseline_ex,
        pipeline_ex=pipeline_ex,
        stage1_miss=s1_miss,
        w_filtered=len(w_filtered),
        w_final=len(w_final),
        fail_resolve=fail_resolve,
        constraints=constraints,
    )

    in_binary = sum(1 for t in qa_trace if t.get("oracle") in ("yes", "no"))

    return {
        "qid": qid,
        "gold_axis_n": len(gold_axes),
        "stage1_miss_axis": s1_miss,
        "w_raw": len(raw_worlds),
        "w_filtered": len(w_filtered),
        "w_final": len(w_final),
        "drop_stats": drop_stats,
        "rounds": rounds,
        "regen_triggered": regen,
        "fail_resolve": fail_resolve,
        "constraints": [{"axis": a, "value": v} for a, v in constraints],
        "qa_trace": qa_trace,
        "baseline_sql": baseline_sql,
        "baseline_ex": baseline_ex,
        "baseline_exec_hash": baseline_h,
        "pipeline_sql": pipeline_sql,
        "pipeline_ex": pipeline_ex,
        "pipeline_exec_hash": pipeline_h,
        "gold_exec_hash": gold_hash,
        "failure_bucket": bucket,
        "oracle_in_binary": in_binary,
        "slots": [{"slot_id": s.slot_id, "axis": s.axis_name, "n_candidates": len(s.candidates)} for s in slots],
    }


def write_report(cases: list[dict], out_md: Path) -> None:
    n = len(cases)
    ex_b = statistics.mean(c["baseline_ex"] for c in cases) if cases else 0
    ex_p = statistics.mean(c["pipeline_ex"] for c in cases) if cases else 0
    delta = ex_p - ex_b

    b_disc = sum(1 for c in cases if c["baseline_ex"] and not c["pipeline_ex"])
    c_disc = sum(1 for c in cases if not c["baseline_ex"] and c["pipeline_ex"])
    p_mcn = mcnemar_two_sided(b_disc, c_disc)
    p_one = mcnemar_p_value(c_disc, b_disc)  # one-sided: pipeline-only discordant > baseline-only

    raw_ws = [c["w_raw"] for c in cases]
    filt_ws = [c["w_filtered"] for c in cases]
    prune_rates = [
        1 - c["w_filtered"] / c["w_raw"] if c["w_raw"] else 0 for c in cases
    ]
    zero_filt = sum(1 for c in cases if c["w_filtered"] == 0)
    full_pass = sum(1 for c in cases if c["w_raw"] and c["w_filtered"] == c["w_raw"])

    buckets: dict[str, int] = {}
    for c in cases:
        b = c["failure_bucket"]
        buckets[b] = buckets.get(b, 0) + 1

    lines: list[str] = []
    w = lines.append
    w("# Sanity Check Path 1+3 — Numbers Only\n\n")
    w(f"Cases: **{n}** | W_raw cap: **{W_RAW_CAP}** | max_rounds: **{MAX_ROUNDS}**\n\n")

    w("## Headline metrics\n\n")
    w(f"| Metric | Value |\n|---|---|\n")
    w(f"| EX@Baseline | {ex_b:.4f} |\n")
    w(f"| EX@Pipeline | {ex_p:.4f} |\n")
    w(f"| **Δ (Pipeline − Baseline)** | **{delta:+.4f}** |\n")
    w(f"| McNemar two-sided p | {p_mcn if p_mcn is not None else 'NA'} |\n")
    w(f"| McNemar one-sided p (pipeline better) | {p_one if p_one is not None else 'NA'} |\n")
    w(f"| Discordant: baseline_ok pipeline_fail (b) | {b_disc} |\n")
    w(f"| Discordant: baseline_fail pipeline_ok (c) | {c_disc} |\n")

    w("\n## Q1: |W| distribution\n\n")
    w("| Stage | Mean | Median | Min | Max | ≤1 | >16 |\n")
    w("|---|---|---|---|---|---|---|\n")
    for label, vals in [("After LLM materialization (raw)", raw_ws), ("After execution filter", filt_ws)]:
        if vals:
            w(
                f"| {label} | {statistics.mean(vals):.2f} | {statistics.median(vals):.1f} | "
                f"{min(vals)} | {max(vals)} | {sum(1 for v in vals if v <= 1)} | "
                f"{sum(1 for v in vals if v > 16)} |\n"
            )

    w("\n## Q2: Execution filter\n\n")
    w(f"- Mean prune rate: **{statistics.mean(prune_rates)*100:.2f}%**\n")
    w(f"- 100% pass cases: **{full_pass}/{n}**\n")
    w(f"- 0 worlds after filter: **{zero_filt}/{n}**\n")

    w("\n## Q3/Q4 secondary\n\n")
    w(f"- Mean rounds: {statistics.mean([c['rounds'] for c in cases]):.2f}\n")
    w(f"- Regenerate triggered: {sum(1 for c in cases if c['regen_triggered'])}/{n}\n")
    w(f"- Mean oracle in-binary answers: {statistics.mean([c['oracle_in_binary'] for c in cases]):.2f}\n")

    w("\n## Failure buckets\n\n")
    w("| Bucket | Count |\n|---|---|\n")
    for name in sorted(buckets.keys()):
        w(f"| {name} | {buckets[name]} |\n")

    w("\n## Per-case table\n\n")
    w("| case_id | gold_axis_n | |W|_raw | |W|_filtered | rounds | regen | baseline_ex | pipeline_ex | bucket |\n")
    w("|---|---|---|---|---|---|---|---|---|\n")
    for c in sorted(cases, key=lambda x: int(x["qid"])):
        w(
            f"| {c['qid']} | {c['gold_axis_n']} | {c['w_raw']} | {c['w_filtered']} | "
            f"{c['rounds']} | {'Y' if c['regen_triggered'] else 'N'} | {c['baseline_ex']} | "
            f"{c['pipeline_ex']} | {c['failure_bucket']} |\n"
        )

    out_md.write_text("".join(lines), encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--resume", action="store_true")
    args = ap.parse_args()

    cfg = load_config()
    examples = {ex.qid: ex for ex in load_bird116(BIRD_CSV, cfg["data"]["bird_db_root"])}
    stage1 = load_stage1()
    qids = sorted(stage1.keys(), key=int)
    if args.limit:
        qids = qids[: args.limit]

    done: dict[str, dict] = {}
    if args.resume and JSON_OUT.exists():
        prev = json.loads(JSON_OUT.read_text())
        for c in prev.get("cases", []):
            done[c["qid"]] = c
        print(f"Resume: {len(done)} cases loaded")

    llm = LLMClient(preset="yi_zhan_gpt-4o")
    cases: list[dict] = []

    for i, qid in enumerate(qids):
        if qid in done:
            cases.append(done[qid])
            continue
        if qid not in examples:
            print(f"skip {qid}")
            continue
        print(f"[{i+1}/{len(qids)}] qid={qid}")
        rec = run_case(qid, stage1[qid], examples[qid], llm)
        cases.append(rec)
        print(
            f"  w_raw={rec['w_raw']} w_filt={rec['w_filtered']} "
            f"base={rec['baseline_ex']} pipe={rec['pipeline_ex']} bucket={rec['failure_bucket']}"
        )
        payload = {"n_cases": len(cases), "cases": sorted(cases, key=lambda x: int(x["qid"]))}
        JSON_OUT.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    cases = sorted(cases, key=lambda x: int(x["qid"]))
    write_report(cases, MD_OUT)
    print(f"Wrote {JSON_OUT}\nWrote {MD_OUT}")


if __name__ == "__main__":
    main()
