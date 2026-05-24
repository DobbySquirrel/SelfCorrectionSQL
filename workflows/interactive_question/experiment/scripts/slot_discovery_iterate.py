#!/usr/bin/env python3
"""
Path B: dev-only prompt iteration + locked test eval for Stage 1 slot discovery.

Usage:
  python experiment/scripts/slot_discovery_iterate.py --step split
  python experiment/scripts/slot_discovery_iterate.py --step v0-eval
  python experiment/scripts/slot_discovery_iterate.py --step iterate
  python experiment/scripts/slot_discovery_iterate.py --step test
  python experiment/scripts/slot_discovery_iterate.py --step report
  python experiment/scripts/slot_discovery_iterate.py --all
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from experiment.data.bird116_loader import load_bird116
from experiment.pipeline.llm_client import LLMClient, load_config
from experiment.scripts.axis_miss_regen_compare import axis_label, extract_predicates
from experiment.scripts import slot_discovery_validation as sdv

SPLIT_JSON = ROOT / "experiment/results/slot_discovery_split.json"
V0_RAW = ROOT / "experiment/results/slot_discovery_raw.jsonl"
V0_SPLIT_EVAL = ROOT / "experiment/results/slot_discovery_v0_split_eval.json"
ITER_LOG = ROOT / "experiment/results/slot_discovery_iteration_log.md"
ITER_RAW = ROOT / "experiment/results/slot_discovery_iteration_dev_raw.jsonl"
V1_TEST_RAW = ROOT / "experiment/results/slot_discovery_v1_test_raw.jsonl"
V1_TEST_EVAL = ROOT / "experiment/results/slot_discovery_v1_test_eval.json"
PATH_B_MD = ROOT / "experiment/results/slot_discovery_path_b.md"
ITER_STATE = ROOT / "experiment/results/slot_discovery_iteration_state.json"
PROMPTS_DIR = ROOT / "prompts"
BIRD_CSV = ROOT / "dataset/ambiguity_116_with_evidence_sql_schema.csv"

PROMPT_VERSIONS = ["v0", "v1", "v2", "v3"]
PROMPT_FILES = {
    "v0": PROMPTS_DIR / "slot_discovery_v0.md",
    "v1": PROMPTS_DIR / "slot_discovery_v1.md",
    "v2": PROMPTS_DIR / "slot_discovery_v2.md",
    "v3": PROMPTS_DIR / "slot_discovery_v3.md",
}


@dataclass
class Metrics:
    axis_recall: float
    value_recall_exact: float
    value_recall_semantic: float
    fp_rate: float
    avg_value_count: float
    n_cases: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "axis_recall": round(self.axis_recall, 4),
            "value_recall_exact": round(self.value_recall_exact, 4),
            "value_recall_semantic": round(self.value_recall_semantic, 4),
            "fp_rate": round(self.fp_rate, 4),
            "avg_value_count": round(self.avg_value_count, 2),
            "n_cases": self.n_cases,
        }

    def score(self) -> float:
        return self.axis_recall + self.value_recall_semantic - 0.5 * self.fp_rate


def load_split() -> dict[str, list[str]]:
    return json.loads(SPLIT_JSON.read_text())


def load_prompt(version: str) -> str:
    return PROMPT_FILES[version].read_text(encoding="utf-8")


def render_prompt(template: str, nl_question: str, schema_str: str) -> str:
    return template.replace("{nl_question}", nl_question).replace("{schema_str}", schema_str)


def load_raw(path: Path, *, prompt_version: str | None = None) -> dict[str, dict]:
    out: dict[str, dict] = {}
    if not path.exists():
        return out
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)
        qid = str(rec["qid"])
        if prompt_version is not None and rec.get("prompt_version") != prompt_version:
            continue
        out[qid] = rec
    return out


def append_raw(path: Path, rec: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def eval_qids(qids: list[str], raw_by_qid: dict[str, dict], examples: dict) -> tuple[list[dict], Metrics]:
    results: list[dict] = []
    for qid in qids:
        ex = examples[qid]
        gold_axes = {axis_label(k): v for k, v in extract_predicates(ex.gold_sql, ex.db_path).items()}
        rec = raw_by_qid.get(qid)
        if not rec or rec.get("status") != "ok":
            raise KeyError(f"Missing raw output for qid={qid}")
        slots = rec["predicted_slots"]
        row = sdv.analyze_case(qid, gold_axes, slots)
        row["nl_question"] = rec.get("nl_question", ex.question_raw or ex.question)
        results.append(row)
    agg = sdv.aggregate(results)
    m = Metrics(
        axis_recall=agg["avg_axis_recall"],
        value_recall_exact=agg["avg_value_recall_exact"],
        value_recall_semantic=agg["avg_value_recall_semantic"],
        fp_rate=agg["avg_false_positive_rate"],
        avg_value_count=agg["avg_slot_value_count"],
        n_cases=len(results),
    )
    return results, m


def run_llm_batch(
    qids: list[str],
    examples: dict,
    llm: LLMClient,
    prompt_template: str,
    *,
    prompt_version: str,
    out_path: Path,
    resume: bool = True,
) -> dict[str, dict]:
    cache = load_raw(out_path, prompt_version=prompt_version) if resume else {}
    for qid in qids:
        if qid in cache and cache[qid].get("status") == "ok":
            continue
        ex = examples[qid]
        nl_q = ex.question_raw or ex.question
        schema = ex.schema_no_content or ex.schema
        prompt = render_prompt(prompt_template, nl_q, schema)
        resp = llm.complete([{"role": "user", "content": prompt}], temperature=0.0, max_tokens=4096)
        if resp.finish_reason and str(resp.finish_reason).startswith("error:"):
            rec = {"qid": qid, "status": "error", "prompt_version": prompt_version, "error": resp.finish_reason}
            append_raw(out_path, rec)
            cache[qid] = rec
            continue
        slots = sdv.parse_json_list(resp.text)
        if slots is None:
            rec = {"qid": qid, "status": "error", "prompt_version": prompt_version, "error": "parse_failed", "raw": resp.text}
            append_raw(out_path, rec)
            cache[qid] = rec
            continue
        rec = {
            "qid": qid,
            "status": "ok",
            "prompt_version": prompt_version,
            "nl_question": nl_q,
            "predicted_slots": slots,
            "raw": resp.text,
        }
        append_raw(out_path, rec)
        cache[qid] = rec
        print(f"  LLM qid={qid} ok slots={len(slots)}")
    return cache


def step_split() -> None:
    from experiment.scripts import slot_discovery_split as split_mod
    split_mod.main()


def step_v0_eval(examples: dict) -> dict[str, Any]:
    split = load_split()
    raw = load_raw(V0_RAW)
    dev_res, dev_m = eval_qids(split["dev"], raw, examples)
    test_res, test_m = eval_qids(split["test"], raw, examples)
    payload = {
        "v0": {"dev": dev_m.to_dict(), "test": test_m.to_dict()},
        "dev_per_case": [{k: r[k] for k in (
            "qid", "axis_recall", "value_recall_exact", "value_recall_semantic", "false_positive_rate", "avg_value_count"
        )} for r in dev_res],
        "test_per_case": [{k: r[k] for k in (
            "qid", "axis_recall", "value_recall_exact", "value_recall_semantic", "false_positive_rate", "avg_value_count"
        )} for r in test_res],
    }
    V0_SPLIT_EVAL.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"v0 dev: {dev_m.to_dict()}")
    print(f"v0 test: {test_m.to_dict()}")
    print(f"Wrote {V0_SPLIT_EVAL}")
    return payload


def diagnose_dev_failures(dev_results: list[dict]) -> str:
    low = sorted(dev_results, key=lambda r: r["axis_recall"])[:3]
    high_fp = sorted(dev_results, key=lambda r: r["false_positive_rate"], reverse=True)[:3]
    parts = [
        f"Low axis recall cases: {', '.join(f'{r['qid']}({r['axis_recall']:.2f})' for r in low)}",
        f"High FP cases: {', '.join(f'{r['qid']}({r['false_positive_rate']:.2f})' for r in high_fp)}",
    ]
    return "; ".join(parts)


def accept_candidate(prev: Metrics, new: Metrics) -> bool:
    if new.fp_rate > 0.40:
        return False
    recall_ok = (new.axis_recall >= prev.axis_recall - 0.01) and (
        new.value_recall_semantic >= prev.value_recall_semantic - 0.01
    )
    improved = (new.axis_recall > prev.axis_recall + 0.01) or (
        new.value_recall_semantic > prev.value_recall_semantic + 0.01
    ) or (new.fp_rate < prev.fp_rate - 0.02)
    return recall_ok and improved


def step_iterate(examples: dict, llm: LLMClient) -> dict[str, Any]:
    split = load_split()
    dev_qids = split["dev"]
    v0_raw = load_raw(V0_RAW)
    _, v0_dev = eval_qids(dev_qids, v0_raw, examples)

    if ITER_RAW.exists() and not load_raw(ITER_RAW):
        ITER_RAW.unlink()

    log_lines = ["# Slot Discovery Iteration Log (Path B)\n\n"]
    log_lines.append(f"## V0 dev baseline\n\n```json\n{json.dumps(v0_dev.to_dict(), indent=2)}\n```\n\n")

    best_version = "v0"
    best_metrics = v0_dev
    best_raw = v0_raw
    current = v0_dev

    iteration_prompts = [
        ("v1", "Failure mode: combined taxonomy labels and high FP from over-generated Column/Row slots. "
         "Change: strict calibration, exact subcategory labels, slot count cap."),
        ("v2", "Failure mode: residual FP from speculative Column/Row Structure slots. "
         "Change: precision-first omit-when-uncertain, require schema table.column in fragments, tighter slot cap."),
        ("v3", "Failure mode: axis recall still below target on dev while FP elevated. "
         "Change: balance precision-first with explicit coverage checklist for Table/Join/Projection/Formula/Boundary/Ranking."),
    ]

    for i, (version, failure_note) in enumerate(iteration_prompts, start=1):
        log_lines.append(f"## Iteration {i} ({version})\n\n")
        log_lines.append(f"- Failure mode observed: {failure_note}\n")
        prompt = load_prompt(version)
        old_text = load_prompt(PROMPT_VERSIONS[PROMPT_VERSIONS.index(version) - 1])
        diff_lines = sum(1 for a, b in zip(prompt.splitlines(), old_text.splitlines()) if a != b)
        diff_lines += abs(len(prompt.splitlines()) - len(old_text.splitlines()))
        log_lines.append(f"- Prompt change: ~{diff_lines} line diffs vs previous version (see prompts/slot_discovery_{version}.md)\n")

        run_llm_batch(dev_qids, examples, llm, prompt, prompt_version=version, out_path=ITER_RAW)
        raw_ver = load_raw(ITER_RAW, prompt_version=version)
        if len(raw_ver) != len(dev_qids):
            log_lines.append(f"- Dev metrics: SKIPPED (only {len(raw_ver)}/{len(dev_qids)} cases)\n")
            log_lines.append("- Decision: revert\n- Rationale: incomplete dev run\n\n")
            continue

        dev_res, new_m = eval_qids(dev_qids, raw_ver, examples)
        log_lines.append(f"- Dev metrics: {new_m.to_dict()}\n")
        log_lines.append(f"- Diagnosis after run: {diagnose_dev_failures(dev_res)}\n")

        prev_m = current
        if accept_candidate(prev_m, new_m) or (new_m.score() > best_metrics.score() and new_m.fp_rate <= 0.40):
            decision = "accept"
            current = new_m
            if new_m.score() >= best_metrics.score():
                best_version = version
                best_metrics = new_m
                best_raw = raw_ver
            rationale = (
                f"score {new_m.score():.3f} vs prev {prev_m.score():.3f}; "
                f"axis {new_m.axis_recall:.3f}, sem {new_m.value_recall_semantic:.3f}, fp {new_m.fp_rate:.3f}"
            )
        else:
            decision = "revert"
            rationale = (
                f"no acceptable gain (axis {new_m.axis_recall:.3f}, sem {new_m.value_recall_semantic:.3f}, "
                f"fp {new_m.fp_rate:.3f}) vs current {current.axis_recall:.3f}/{current.value_recall_semantic:.3f}/{current.fp_rate:.3f}"
            )
        log_lines.append(f"- Decision: {decision}\n- Rationale: {rationale}\n\n")
        print(f"Iteration {i} {version}: {decision} {new_m.to_dict()}")

    ITER_LOG.write_text("".join(log_lines), encoding="utf-8")
    state = {
        "best_prompt_version": best_version,
        "best_dev_metrics": best_metrics.to_dict(),
        "v0_dev_metrics": v0_dev.to_dict(),
    }
    ITER_STATE.write_text(json.dumps(state, indent=2), encoding="utf-8")
    print(f"Best prompt: {best_version} dev={best_metrics.to_dict()}")
    print(f"Wrote {ITER_LOG}")
    return state


def lock_prompt(version: str) -> None:
    """Write locked prompt to / tag target file (always slot_discovery_v1.md)."""
    src = PROMPT_FILES[version]
    locked = PROMPTS_DIR / "slot_discovery_v1.md"
    header = f"<!-- Locked prompt: best dev version = {version} (Path B) -->\n\n"
    locked.write_text(header + src.read_text(encoding="utf-8"), encoding="utf-8")
    print(f"Locked prompt {version} -> {locked}")


def step_test(examples: dict, llm: LLMClient) -> dict[str, Any]:
    split = load_split()
    state = json.loads(ITER_STATE.read_text())
    best = state["best_prompt_version"]
    test_qids = split["test"]
    lock_prompt(best)

    if V1_TEST_RAW.exists():
        V1_TEST_RAW.unlink()

    if best == "v0":
        v0_raw = load_raw(V0_RAW)
        for qid in test_qids:
            rec = dict(v0_raw[qid])
            rec["prompt_version"] = "v0_locked"
            append_raw(V1_TEST_RAW, rec)
        raw = load_raw(V1_TEST_RAW)
        print("Best is v0: reusing v0 test raw (no new LLM calls)")
    else:
        prompt = load_prompt(best)
        run_llm_batch(test_qids, examples, llm, prompt, prompt_version=best, out_path=V1_TEST_RAW, resume=False)
        raw = load_raw(V1_TEST_RAW, prompt_version=best)
    test_res, test_m = eval_qids(test_qids, raw, examples)

    v0_eval = json.loads(V0_SPLIT_EVAL.read_text())
    payload = {
        "locked_prompt_version": best,
        "locked_prompt_file": "prompts/slot_discovery_v1.md",
        "test_metrics": test_m.to_dict(),
        "v0_test_metrics": v0_eval["v0"]["test"],
        "dev_best_metrics": state["best_dev_metrics"],
        "per_case": test_res,
        "gap_axis_recall_pp": round(
            (state["best_dev_metrics"]["axis_recall"] - test_m.axis_recall) * 100, 2,
        ),
        "note": "v0 raw reused on test" if best == "v0" else None,
    }
    V1_TEST_EVAL.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Test metrics ({best}): {test_m.to_dict()}")
    print(f"Wrote {V1_TEST_RAW}\nWrote {V1_TEST_EVAL}")
    return payload


def step4_verdict(v0_test: Metrics, dev_best: Metrics, test_v1: Metrics) -> tuple[str, str]:
    gap_pp = abs(dev_best.axis_recall - test_v1.axis_recall) * 100
    if gap_pp >= 10:
        return (
            "overfit",
            "Overfit detected: |dev-test| axis recall >= 10pp; revert to v0 for paper numbers.",
        )
    if test_v1.axis_recall >= 0.85 and test_v1.fp_rate <= 0.35 and gap_pp < 10:
        return ("pass", "Pass: test axis recall >= 0.85, FP <= 0.35, dev-test gap < 10pp; prompt locked for paper.")
    if (
        abs(test_v1.axis_recall - v0_test.axis_recall) < 0.03
        and abs(test_v1.value_recall_semantic - v0_test.value_recall_semantic) < 0.03
    ):
        return ("no_improvement", "No improvement vs v0 on test (<3pp); accept v0 baseline for paper.")
    if 0.78 <= test_v1.axis_recall < 0.85 or (0.35 < test_v1.fp_rate <= 0.42):
        return ("marginal_pass", "Marginal pass: test metrics in marginal band; use locked test numbers with tuning framing.")
    return ("marginal_pass", "Marginal pass: improvement vs v0 but below strong thresholds; use test v1 numbers.")


def step_report(examples: dict) -> None:
    split = load_split()
    v0_eval = json.loads(V0_SPLIT_EVAL.read_text())
    state = json.loads(ITER_STATE.read_text())
    test_eval = json.loads(V1_TEST_EVAL.read_text())
    iter_log = ITER_LOG.read_text() if ITER_LOG.exists() else ""

    best_ver = state["best_prompt_version"]
    dev_best = Metrics(**state["best_dev_metrics"])
    v0_dev = Metrics(**v0_eval["v0"]["dev"])
    v0_test = Metrics(**v0_eval["v0"]["test"])
    test_m = Metrics(**test_eval["test_metrics"])

    verdict_key, verdict_text = step4_verdict(v0_test, dev_best, test_m)

    # pick traces from test per_case
    per_case = test_eval["per_case"]
    # traces: 2 high recall, 1 mid, 1 low, 1 fp-heavy (dedupe)
    by_recall = sorted(per_case, key=lambda r: r["axis_recall"], reverse=True)
    high = [r for r in by_recall if r["axis_recall"] >= 0.85][:2]
    if len(high) < 2:
        high = by_recall[:2]
    low = [r for r in by_recall if r["axis_recall"] < 0.5][:1] or [by_recall[-1]]
    mid_pool = [r for r in by_recall if 0.5 <= r["axis_recall"] < 0.85]
    mid = mid_pool[len(mid_pool) // 2 : len(mid_pool) // 2 + 1] if mid_pool else [by_recall[len(by_recall) // 2]]
    fp_heavy = sorted(per_case, key=lambda r: r["false_positive_rate"], reverse=True)[:1]
    trace_qids: list[str] = []
    for group in (high, mid, low, fp_heavy):
        for r in group:
            if r["qid"] not in trace_qids:
                trace_qids.append(r["qid"])
    trace_qids = trace_qids[:5]

    lines: list[str] = []
    w = lines.append
    w("# Stage 1 Slot Discovery — Path B Report\n\n")

    w("## Section 1: Split protocol & lock-down\n\n")
    w(f"- Dev ({len(split['dev'])}): `{', '.join(split['dev'])}`\n")
    w(f"- Test ({len(split['test'])}): `{', '.join(split['test'])}`\n")
    w(f"- Split file: `experiment/results/slot_discovery_split.json`\n")
    w(f"- Locked prompt: `prompts/slot_discovery_v1.md` (source: **{best_ver}**; tag: `slot-discovery-v1-locked`)\n\n")

    w("## Section 2: V0 baseline on dev/test\n\n")
    w("| split | axis_recall | value_sem | value_exact | fp_rate | avg_value_count |\n")
    w("|---|---|---|---|---|---|\n")
    for name in ("dev", "test"):
        m = v0_eval["v0"][name]
        w(f"| {name} | {m['axis_recall']:.4f} | {m['value_recall_semantic']:.4f} | "
          f"{m['value_recall_exact']:.4f} | {m['fp_rate']:.4f} | {m['avg_value_count']} |\n")

    w("\n## Section 3: Iteration log\n\n")
    w(iter_log if iter_log else "(see slot_discovery_iteration_log.md)\n")

    w("\n## Section 4: Locked prompt results (v0 vs best)\n\n")
    w("| split | version | axis_recall | value_sem | value_exact | fp_rate | avg_value_count |\n")
    w("|---|---|---|---|---|---|---|\n")
    w(f"| dev | v0 | {v0_dev.axis_recall:.4f} | {v0_dev.value_recall_semantic:.4f} | "
      f"{v0_dev.value_recall_exact:.4f} | {v0_dev.fp_rate:.4f} | {v0_dev.avg_value_count} |\n")
    w(f"| dev | {best_ver} | {dev_best.axis_recall:.4f} | {dev_best.value_recall_semantic:.4f} | "
      f"{dev_best.value_recall_exact:.4f} | {dev_best.fp_rate:.4f} | {dev_best.avg_value_count} |\n")
    w(f"| test | v0 | {v0_test.axis_recall:.4f} | {v0_test.value_recall_semantic:.4f} | "
      f"{v0_test.value_recall_exact:.4f} | {v0_test.fp_rate:.4f} | {v0_test.avg_value_count} |\n")
    w(f"| test | {best_ver} | {test_m.axis_recall:.4f} | {test_m.value_recall_semantic:.4f} | "
      f"{test_m.value_recall_exact:.4f} | {test_m.fp_rate:.4f} | {test_m.avg_value_count} |\n")

    w("\n### Dev-test gap (Step 4)\n\n")
    w("| metric | dev v0 | dev best | test locked |\n")
    w("|---|---|---|---|\n")
    w(f"| axis recall | {v0_dev.axis_recall:.4f} | {dev_best.axis_recall:.4f} | {test_m.axis_recall:.4f} |\n")
    w(f"| value recall (sem) | {v0_dev.value_recall_semantic:.4f} | {dev_best.value_recall_semantic:.4f} | {test_m.value_recall_semantic:.4f} |\n")
    w(f"| value recall (exact) | {v0_dev.value_recall_exact:.4f} | {dev_best.value_recall_exact:.4f} | {test_m.value_recall_exact:.4f} |\n")
    w(f"| FP rate | {v0_dev.fp_rate:.4f} | {dev_best.fp_rate:.4f} | {test_m.fp_rate:.4f} |\n")
    w(f"| avg value count | {v0_dev.avg_value_count} | {dev_best.avg_value_count} | {test_m.avg_value_count} |\n")

    w("\n### Test per-case (15)\n\n")
    w("| qid | gold_axis | pred_axis | axis_recall | value_sem | fp_rate |\n")
    w("|---|---|---|---|---|---|\n")
    for r in sorted(per_case, key=lambda x: int(x["qid"])):
        w(f"| {r['qid']} | {r['gold_axis_count']} | {r['predicted_axis_count']} | "
          f"{r['axis_recall']:.3f} | {r['value_recall_semantic']} | {r['false_positive_rate']:.3f} |\n")

    w("\n## Section 5: Test traces\n\n")
    by_qid = {r["qid"]: r for r in per_case}
    for qid in trace_qids:
        r = by_qid[qid]
        w(f"### qid={qid}\n\n")
        w(f"**NL:** {r.get('nl_question', '')[:250]}...\n\n")
        w("**Gold axes:**\n")
        for ax, val in sorted(r["gold_axes"].items()):
            w(f"- `{ax}`: `{str(val)[:70]}`\n")
        w("\n**Predicted slots (mapped):**\n")
        for ax, vals in sorted(r.get("predicted_atomic_candidates", {}).items()):
            w(f"- `{ax}`: {len(vals)} candidates\n")
        w(f"\n**Commentary:** axis_recall={r['axis_recall']:.2f}, fp={r['false_positive_rate']:.2f}, "
          f"value_sem={r['value_recall_semantic']}.\n\n")

    w("## Section 6: 判断\n\n")
    w(f"**Verdict ({verdict_key}):** {verdict_text}\n")
    w(f"\nDev-test axis recall gap: {test_eval['gap_axis_recall_pp']} pp\n")

    PATH_B_MD.write_text("".join(lines), encoding="utf-8")
    print(f"Wrote {PATH_B_MD}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--step", choices=["split", "v0-eval", "iterate", "test", "report", "all"], default="all")
    args = ap.parse_args()

    cfg = load_config()
    examples = {ex.qid: ex for ex in load_bird116(BIRD_CSV, cfg["data"]["bird_db_root"])}

    if args.step in ("split", "all"):
        step_split()
    if args.step in ("v0-eval", "all"):
        if not SPLIT_JSON.exists():
            step_split()
        step_v0_eval(examples)
    llm = LLMClient(preset="yi_zhan_gpt-4o")
    if args.step in ("iterate", "all"):
        if not V0_SPLIT_EVAL.exists():
            step_v0_eval(examples)
        step_iterate(examples, llm)
    if args.step in ("test", "all"):
        if not ITER_STATE.exists():
            step_iterate(examples, llm)
        step_test(examples, llm)
    if args.step in ("report", "all"):
        step_report(examples)


if __name__ == "__main__":
    main()
