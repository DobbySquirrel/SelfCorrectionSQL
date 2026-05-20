#!/usr/bin/env python3
"""Phase C: offline EX@K replay (pool × selector × gold trial)."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from experiment.pipeline.ast import dsl_available
from experiment.pipeline.ast.token_diff import is_syntactic_only_question
from experiment.pipeline.candidates import Candidate, build_candidates
from experiment.pipeline.executor import execute_sql
from experiment.pipeline.interactive_loop import run_interaction
from experiment.pipeline.items import items_from_candidates
from experiment.pipeline.llm_client import LLMClient, load_config
from experiment.pipeline.openworld.cache import JSONLCache
from experiment.pipeline.openworld.generator import OpenWorldGenerator
from experiment.pipeline.openworld_config import OpenWorldConfig, default_epsilon_0
from experiment.pipeline.selectors import World, build_pool, build_selector, items_from_worlds
from experiment.pipeline.user_simulator import UserSimulator
from experiment.pipeline.executor import execute_sql

REAL_TYPES = {"attachment", "scope", "vague"}
PARA_TYPES = {"aggregate", "column", "join", "table"}
BIRD_CSV = ROOT / "dataset" / "ambiguity_116_with_evidence_sql_schema.csv"


def load_jsonl(path: str | Path) -> list[dict]:
  rows = []
  with open(path) as f:
    for line in f:
      line = line.strip()
      if line:
        rows.append(json.loads(line))
  return rows


def _load_example_lookup(cfg: dict, dataset: str) -> dict[str, object]:
  if dataset == "bird116":
    from experiment.data.bird116_loader import load_bird116
    exs = load_bird116(BIRD_CSV, cfg["data"]["bird_db_root"])
    return {e.qid: e for e in exs}
  from experiment.data.loader import load_clambsql
  exs = load_clambsql(cfg["data"]["clambsql"], cfg["data"]["clambsql_db_root"])
  return {e.qid: e for e in exs}


def _gold_sql_for_trial(
    ex: object,
    gold_hash: str,
    db_path: str,
    timeout: float,
) -> str:
  for sql in getattr(ex, "candidate_sqls", []) or []:
    if not sql:
      continue
    r = execute_sql(db_path, str(sql), timeout_s=timeout)
    if r.ok and r.result_hash == gold_hash:
      return str(sql).strip()
  gs = (getattr(ex, "gold_sql", "") or "").strip()
  if gs:
    r = execute_sql(db_path, gs, timeout_s=timeout)
    if r.ok and r.result_hash == gold_hash:
      return gs
  return gs


def load_gold_lookup(path: str | Path, dataset: str) -> dict[str, dict]:
  look: dict[str, dict] = {}
  for r in load_jsonl(path):
    qid = str(r["qid"])
    if dataset == "bird116":
      look[qid] = r
    else:
      look[qid] = r
  return look


def build_worlds(
    row: dict,
    db_path: str,
    timeout: float,
) -> tuple[list[World], set[str]]:
  sqls = row.get("sampled_sqls") or []
  counts: Counter[str] = Counter()
  rep_sql: dict[str, str] = {}

  for sql in sqls:
    if not sql or not str(sql).strip():
      continue
    r = execute_sql(db_path, str(sql), timeout_s=timeout)
    if r.ok and r.result_hash:
      h = r.result_hash
      counts[h] += 1
      rep_sql.setdefault(h, str(sql))

  worlds = [
    World(hash=h, sample_count=counts[h], representative_sql=rep_sql[h])
    for h in counts
  ]
  return worlds, set(counts.keys())


def qa_summary(history) -> list[dict]:
  out = []
  for rec in history:
    q = rec.question
    idx = rec.chosen_index
    label = "o_none" if idx < 0 else q.options[idx].label[:60]
    out.append({
      "source": q.source,
      "chosen_index": idx,
      "chosen_label": label,
      "eig_value": q.metadata.get("eig_value"),
      "syntactic_only": getattr(rec, "syntactic_only", None),
    })
  return out


def _slice_key(dataset: str, row: dict) -> str:
  if dataset == "bird116":
    return "bird116"
  t = row.get("ambig_type", "")
  if t in REAL_TYPES:
    return "clambsql_real"
  if t in PARA_TYPES:
    return "clambsql_para"
  return "clambsql_other"


def _open_world_stats(results: list[dict]) -> None:
  """Explain open_world counts (trial-row unit vs example-level gold_in_sampled)."""
  ok = [r for r in results if not r.get("excluded")]
  if not ok:
    return

  ow_all = sum(1 for r in ok if r.get("open_world_triggered"))
  ow_trial_out = sum(
      1 for r in ok
      if r.get("open_world_triggered") and not r.get("trial_gold_in_sampled", True)
  )
  ow_trial_in = sum(
      1 for r in ok
      if r.get("open_world_triggered") and r.get("trial_gold_in_sampled")
  )
  rows_trial_out = sum(1 for r in ok if not r.get("trial_gold_in_sampled"))
  rows_trial_in = sum(1 for r in ok if r.get("trial_gold_in_sampled"))

  # |W|>=2 & trial gold ∉ W: open_world expected on first question
  ow_expected_ctx = sum(
      1 for r in ok
      if r.get("open_world_triggered")
      and not r.get("trial_gold_in_sampled")
      and r.get("n_unique_sampled", 0) >= 2
  )
  ctx_rows = sum(
      1 for r in ok
      if not r.get("trial_gold_in_sampled") and r.get("n_unique_sampled", 0) >= 2
  )

  print(f"\nOpen-world (unit = trial-row = example × pool × selector × gold_trial):")
  print(f"  triggered: {ow_all} / {len(ok)} rows ({100*ow_all/len(ok):.1f}%)")
  print(f"  trial_gold ∉ W_sampled: {ow_trial_out} / {rows_trial_out} rows "
        f"({100*ow_trial_out/(rows_trial_out or 1):.1f}%) — expected")
  print(f"  trial_gold ∈ W_sampled: {ow_trial_in} rows — "
        f"{'OK' if ow_trial_in == 0 else 'check multi-gold trial / partition'}")
  if ctx_rows:
    print(f"  (|W|≥2 & trial_gold∉W) triggered: {ow_expected_ctx}/{ctx_rows} "
          f"({100*ow_expected_ctx/ctx_rows:.1f}%)")
  print("  Note: example-level gold_in_sampled uses ANY gold hash; "
        "multi-gold CLAMBSQL trials can have gold_in_sampled=True but "
        "trial_gold_in_sampled=False → open_world is not a bug.")


def _atomic_empty_stats(results: list[dict]) -> tuple[int, int, dict[str, tuple[int, int]]]:
  """
  Per-example AtomicPool empty rate on |W|>=2 (dedupe by qid).

  Returns (empty_count, eligible_count, per_slice_counts).
  """
  by_qid: dict[str, dict] = {}
  for r in results:
    if r.get("excluded"):
      continue
    if r.get("requested_pool") != "atomic":
      continue
    if r.get("n_unique_sampled", 0) < 2:
      continue
    qid = str(r["qid"])
    if qid in by_qid:
      continue
    by_qid[qid] = r

  per_slice: dict[str, tuple[int, int]] = defaultdict(lambda: (0, 0))
  empty_n = 0
  for r in by_qid.values():
    sk = r.get("dataset_slice", "unknown")
    e, t = per_slice[sk]
    t += 1
    if r.get("atomic_pool_empty"):
      empty_n += 1
      e += 1
    per_slice[sk] = (e, t)

  return empty_n, len(by_qid), dict(per_slice)


def _token_empty_stats(results: list[dict]) -> tuple[int, int, dict[str, tuple[int, int]]]:
  """Per-example TokenDiffPool empty rate on n_candidates>=2 (dedupe by qid)."""
  by_qid: dict[str, dict] = {}
  for r in results:
    if r.get("excluded"):
      continue
    if r.get("requested_pool") != "token":
      continue
    if r.get("n_unique_candidates", 0) < 2:
      continue
    qid = str(r["qid"])
    if qid in by_qid:
      continue
    by_qid[qid] = r

  per_slice: dict[str, tuple[int, int]] = defaultdict(lambda: (0, 0))
  empty_n = 0
  for r in by_qid.values():
    sk = r.get("dataset_slice", "unknown")
    e, t = per_slice[sk]
    t += 1
    if r.get("token_pool_empty"):
      empty_n += 1
      e += 1
    per_slice[sk] = (e, t)

  return empty_n, len(by_qid), dict(per_slice)


def print_summary(
    results: list[dict],
    dataset: str,
    K_max: int,
) -> None:
  stats: dict[tuple, list[dict]] = defaultdict(list)
  for r in results:
    if r.get("excluded"):
      continue
    sk = _slice_key(dataset, r)
    key = (sk, r["pool"], r["selector"], r.get("gold_in_sampled"))
    stats[key].append(r)

  slices = sorted({k[0] for k in stats})
  for sk in slices:
    for gold_in in (True, False):
      print(f"\n=== {sk}, gold {'∈' if gold_in else '∉'} W_sampled ===")
      print(f"{'pool':<8} {'selector':<10} ", end="")
      for k in range(K_max + 1):
        print(f"{'EX@'+str(k):>8}", end="")
      print(f"{'avg_K':>8} {'n':>6}")
      for pool in ("cluster", "token", "atomic"):
        for sel in ("random", "max_prob", "eig"):
          rs = stats.get((sk, pool, sel, gold_in), [])
          if not rs:
            continue
          n = len(rs)
          ex_cols = []
          for k in range(K_max + 1):
            ex_cols.append(sum(1 for r in rs if r["ex_at_k"][k]) / n)
          avg_k = sum(r["n_questions_asked"] for r in rs) / n
          print(f"{pool:<8} {sel:<10} ", end="")
          for v in ex_cols:
            print(f"{v:8.3f}", end="")
          print(f"{avg_k:8.2f} {n:6d}")

  excluded = sum(1 for r in results if r.get("excluded"))
  open_w = sum(1 for r in results if r.get("open_world_triggered"))
  fallback = sum(1 for r in results if r.get("atomic_fallback"))
  ok_rows = [r for r in results if not r.get("excluded")]
  oracle = [r for r in ok_rows if r.get("trial_gold_in_sampled")]
  if oracle:
    print(f"\nOracle ceiling (trial-row, THIS trial's gold ∈ W_sampled): "
          f"{len(oracle)}/{len(ok_rows)} "
          f"({100*len(oracle)/len(ok_rows):.1f}%)")
    print("  (CLAMBSQL multi-gold: example may have gold_in_sampled=True but "
          "trial_gold_in_sampled=False for some trials)")

  empty_n, eligible_n, per_slice = _atomic_empty_stats(results)
  if eligible_n:
    pct = 100.0 * empty_n / eligible_n
    print(f"\nAtomicPool empty rate (only on |W|>=2 examples): "
          f"{empty_n} / {eligible_n} ({pct:.1f}%)")
    if pct > 20.0:
      print("  ⚠ >20% — after full run, inspect representative_sql / DSL failures.")
    for sk in sorted(per_slice):
      e, t = per_slice[sk]
      if t:
        print(f"    {sk}: {e}/{t} ({100*e/t:.1f}%)")
  else:
    print("\nAtomicPool empty rate (|W|>=2): N/A (no atomic runs on |W|>=2)")

  tok_empty, tok_elig, tok_slice = _token_empty_stats(results)
  if tok_elig:
    pct = 100.0 * tok_empty / tok_elig
    print(f"\nTokenDiffPool empty rate (n_candidates>=2): "
          f"{tok_empty} / {tok_elig} ({pct:.1f}%)")
    for sk in sorted(tok_slice):
      e, t = tok_slice[sk]
      if t:
        print(f"    {sk}: {e}/{t} ({100*e/t:.1f}%)")

  tok_rows = [
      r for r in ok_rows
      if r.get("pool") == "token" and r.get("q_syntactic_only_per_turn")
  ]
  if tok_rows:
    turns = sum(len(r["q_syntactic_only_per_turn"]) for r in tok_rows)
    syn_turns = sum(
        sum(1 for x in r["q_syntactic_only_per_turn"] if x)
        for r in tok_rows
    )
    print(f"\nToken syntactic-only question rate (per asked turn): "
          f"{syn_turns}/{turns} ({100*syn_turns/(turns or 1):.1f}%)")

  _open_world_stats(results)

  print(f"\nExcluded (gold unavailable): {excluded}")
  print(f"Atomic→cluster fallback:     {fallback} trial-rows")


def main() -> None:
  ap = argparse.ArgumentParser()
  ap.add_argument("--input", required=True,
                  help="Phase A/B jsonl with sampled_sqls")
  ap.add_argument("--gold", required=True,
                  help="13a or 13b gold hash jsonl")
  ap.add_argument("--dataset", choices=("clambsql", "bird116"), required=True)
  ap.add_argument("--pools", default="cluster,atomic,token",
                  help="cluster,atomic,token or both (=cluster,atomic)")
  ap.add_argument("--selectors", default="random,max_prob,eig")
  ap.add_argument("--K-max", type=int, default=2)
  ap.add_argument("--seed", type=int, default=0)
  ap.add_argument("--timeout", type=float, default=10.0)
  ap.add_argument("--db-root", default=None,
                  help="override db root (bird116 dev_databases)")
  ap.add_argument("--out", default=None)
  ap.add_argument("--limit", type=int, default=0)
  ap.add_argument("--filter-real", action="store_true",
                  help="clambsql: only attachment+scope+vague")
  ap.add_argument("--filter-para", action="store_true",
                  help="clambsql: only paraphrase 4 types")
  ap.add_argument("--enable-instantiation", action="store_true",
                  help="open-world: LLM space instantiation on answer -1")
  ap.add_argument("--hint-mode", choices=("schema_signature", "none"),
                  default="schema_signature")
  ap.add_argument("--epsilon-0", type=float, default=None,
                  help="w_other mass (default per dataset slice)")
  ap.add_argument("--alpha", type=float, default=0.9)
  ap.add_argument("--n-gen", type=int, default=8,
                  help="SQL count per instantiation LLM call")
  ap.add_argument("--max-llm-calls", type=int, default=1)
  ap.add_argument("--openworld-cache-path", default=None)
  ap.add_argument("--llm-preset", default="yi_zhan_gpt-4o",
                  help="LLM preset for open-world generation")
  ap.add_argument("--use-nl-rendering", action="store_true",
                  help="atomic pool: LLM-render NL options (default: DSL labels)")
  args = ap.parse_args()

  cfg = load_config()
  if args.db_root:
    db_root = Path(args.db_root)
  elif args.dataset == "bird116":
    db_root = Path(cfg["data"]["bird_db_root"])
  else:
    db_root = Path(cfg["data"]["clambsql_db_root"])

  pool_names = [p.strip() for p in args.pools.split(",") if p.strip()]
  if "both" in pool_names:
    pool_names = ["cluster", "atomic"]
  if "all" in pool_names:
    pool_names = ["cluster", "atomic", "token"]
  sel_names = [s.strip() for s in args.selectors.split(",") if s.strip()]

  rows_in = load_jsonl(args.input)
  if args.filter_real:
    rows_in = [r for r in rows_in if r.get("ambig_type") in REAL_TYPES]
  if args.filter_para:
    rows_in = [r for r in rows_in if r.get("ambig_type") in PARA_TYPES]
  if args.limit:
    rows_in = rows_in[: args.limit]

  gold_lookup = load_gold_lookup(args.gold, args.dataset)

  db_path_by_qid: dict[str, str] = {}
  for qid, g in gold_lookup.items():
    if g.get("db_path"):
      db_path_by_qid[qid] = g["db_path"]
      continue
    db_id = g.get("db_id", "")
    if db_id and args.dataset == "bird116":
      db_path_by_qid[qid] = str(db_root / db_id / f"{db_id}.sqlite")

  ts = datetime.now().strftime("%Y%m%d_%H%M%S")
  out_path = Path(args.out or f"experiment/runs/phaseC_{args.dataset}_{ts}.jsonl")
  out_path.parent.mkdir(parents=True, exist_ok=True)

  dsl_ok = dsl_available()
  results: list[dict] = []
  atomic_probe_cache: dict[str, dict] = {}
  token_probe_cache: dict[str, dict] = {}

  example_lookup: dict[str, object] = {}
  ow_generator: OpenWorldGenerator | None = None
  nl_llm_client: LLMClient | None = None
  if args.enable_instantiation or args.use_nl_rendering:
    example_lookup = _load_example_lookup(cfg, args.dataset)
  if args.enable_instantiation:
    cache_path = args.openworld_cache_path or (
        f"experiment/cache/openworld_{args.dataset}_{args.hint_mode}.jsonl"
    )
    ow_generator = OpenWorldGenerator(
        LLMClient(preset=args.llm_preset),
        JSONLCache(cache_path),
    )
  if args.use_nl_rendering:
    nl_llm_client = LLMClient(preset=args.llm_preset)

  for row in rows_in:
    qid = str(row["qid"])
    gold_rec = gold_lookup.get(qid)
    if not gold_rec:
      results.append({"qid": qid, "excluded": True, "reason": "no_gold"})
      continue

    db_path = db_path_by_qid.get(qid)
    if not db_path or not Path(db_path).exists():
      results.append({"qid": qid, "excluded": True, "reason": "no_db"})
      continue

    worlds, sampled_set = build_worlds(row, db_path, args.timeout)
    candidates = build_candidates(
        row.get("sampled_sqls") or [], db_path, args.timeout,
    )
    if not worlds and not candidates:
      results.append({"qid": qid, "excluded": True, "reason": "no_worlds"})
      continue

    if args.dataset == "bird116":
      gw = gold_rec.get("gold_world_hash")
      if not gw or not gold_rec.get("ok"):
        results.append({"qid": qid, "excluded": True, "reason": "gold_exec_fail"})
        continue
      trials = [gw]
    else:
      trials = list(gold_rec.get("gold_hashes") or [])
      if not trials:
        results.append({"qid": qid, "excluded": True, "reason": "no_gold_hashes"})
        continue

    slice_key = _slice_key(args.dataset, row)
    eps0 = (
        args.epsilon_0
        if args.epsilon_0 is not None
        else default_epsilon_0(args.dataset, slice_key)
    )
    ex_obj = example_lookup.get(qid)
    question_text = row.get("question") or getattr(ex_obj, "question", "") or ""
    base = {
      "qid": qid,
      "dataset": args.dataset,
      "dataset_slice": slice_key,
      "db_id": gold_rec.get("db_id"),
      "n_unique_sampled": len(worlds),
      "n_unique_candidates": len(candidates),
      "gold_in_sampled": any(t in sampled_set for t in trials),
    }
    if args.dataset == "bird116":
      base["n_anchors"] = row.get("n_anchors")
      base["primary_subtypes"] = row.get("primary_subtypes")
    else:
      base["ambig_type"] = row.get("ambig_type")

    for pool_name in pool_names:
      requested_pool = pool_name
      use_pool = pool_name
      atomic_fallback = False
      atomic_pool_empty: bool | None = None
      atomic_pool_size: int | None = None
      atomic_fallback_reason: str | None = None
      token_pool_empty: bool | None = None
      token_pool_size: int | None = None

      if pool_name == "token":
        if qid not in token_probe_cache:
          meta: dict = {"empty": False, "size": 0}
          if len(candidates) < 2:
            meta = {"empty": None, "size": 0}
          else:
            tp = build_pool("token")
            probe = tp.build(items_from_candidates(candidates))
            meta = {
              "empty": len(probe) == 0,
              "size": len(probe),
            }
          token_probe_cache[qid] = meta
        tmeta = token_probe_cache[qid]
        token_pool_size = tmeta["size"]
        token_pool_empty = (
            bool(tmeta["empty"]) if tmeta["empty"] is not None else None
        )
        pool_builder = build_pool("token")
        interaction_items = items_from_candidates(candidates)
      elif pool_name == "atomic":
        if qid not in atomic_probe_cache:
          meta: dict = {
            "empty": False,
            "size": 0,
            "reason": None,
          }
          if not dsl_ok:
            meta = {"empty": True, "size": 0, "reason": "dsl_unavailable"}
          elif len(worlds) < 2:
            meta = {"empty": None, "size": 0, "reason": "w_lt_2"}
          else:
            ap = build_pool(
                "atomic",
                db_path=db_path,
                use_nl_rendering=args.use_nl_rendering,
                llm_client=nl_llm_client,
                question_text=question_text,
            )
            probe = ap.build(items_from_worlds(worlds))
            meta = {
              "empty": len(probe) == 0,
              "size": len(probe),
              "reason": "empty_pool" if len(probe) == 0 else None,
            }
          atomic_probe_cache[qid] = meta

        meta = atomic_probe_cache[qid]
        atomic_pool_size = meta["size"]
        if meta["reason"] == "w_lt_2":
          atomic_pool_empty = None
        else:
          atomic_pool_empty = bool(meta["empty"])

        if meta["reason"] in ("dsl_unavailable", "empty_pool"):
          pool_builder = build_pool("cluster", db_path=db_path)
          use_pool = "cluster"
          atomic_fallback = True
          atomic_fallback_reason = meta["reason"]
        else:
          pool_builder = build_pool(
              "atomic",
              db_path=db_path,
              use_nl_rendering=args.use_nl_rendering,
              llm_client=nl_llm_client,
              question_text=question_text,
          )
        interaction_items = items_from_worlds(worlds)
      else:
        pool_builder = build_pool(pool_name, db_path=db_path)
        interaction_items = items_from_worlds(worlds)

      def _syn_check(q, items_live):
        live_c = [
            Candidate(
              key=it.key,
              sql=it.representative_sql,
              exec_hash=it.exec_hash,
              weight=it.weight,
            )
            for it in items_live
        ]
        return is_syntactic_only_question(q, {c.key: c for c in live_c})

      for sel_name in sel_names:
        for trial_idx, gw in enumerate(trials):
          sim = UserSimulator(gw)
          with_other = args.enable_instantiation and sel_name == "eig"
          sel = build_selector(
              sel_name,
              seed=args.seed + trial_idx,
              epsilon_0=eps0,
              alpha=args.alpha,
              with_other=with_other,
          )

          ow_cfg = OpenWorldConfig(enabled=False)
          if args.enable_instantiation and ex_obj is not None:
            gold_sql = _gold_sql_for_trial(ex_obj, gw, db_path, args.timeout)
            ow_cfg = OpenWorldConfig(
                enabled=True,
                hint_mode=args.hint_mode,
                epsilon_0=eps0,
                alpha=args.alpha,
                n_gen=args.n_gen,
                max_llm_calls=args.max_llm_calls,
                qid=qid,
                question_text=getattr(ex_obj, "question", ""),
                schema_text=getattr(ex_obj, "schema", ""),
                gold_sql=gold_sql,
                schema_meta={"db_id": getattr(ex_obj, "db_id", "")},
                db_path=db_path,
                timeout=args.timeout,
                generator=ow_generator,
                use_sql_keys=(use_pool == "token"),
            )

          syn_checker = _syn_check if pool_name == "token" else None
          trace = run_interaction(
            interaction_items,
            sim,
            sel,
            pool_builder,
            args.K_max,
            gw,
            early_stop=True,
            syntactic_checker=syn_checker,
            openworld=ow_cfg,
          )

          out_row = {
            **base,
            "requested_pool": requested_pool,
            "pool": use_pool,
            "selector": sel_name,
            "trial_idx": trial_idx,
            "gold_world_hash": gw,
            "trial_gold_in_sampled": gw in sampled_set,
            "ex_at_k": trace.ex_at_k,
            "n_questions_asked": trace.n_questions_asked,
            "open_world_triggered": trace.open_world_triggered,
            "early_stop_reason": trace.early_stop_reason,
            "qa_history_summary": qa_summary(trace.qa_history),
            "q_syntactic_only_per_turn": trace.q_syntactic_only_per_turn,
            "atomic_fallback": atomic_fallback,
            "atomic_fallback_reason": atomic_fallback_reason,
            "atomic_pool_empty": atomic_pool_empty,
            "atomic_pool_size": atomic_pool_size,
            "token_pool_empty": token_pool_empty,
            "token_pool_size": token_pool_size,
            "excluded": False,
            **trace.openworld_meta,
            "llm_calls_used": trace.llm_calls_used,
          }
          results.append(out_row)

  with open(out_path, "w") as f:
    for r in results:
      f.write(json.dumps(r, ensure_ascii=False) + "\n")

  print(f"wrote {len(results)} rows to {out_path}")
  print(f"dsl_available: {dsl_ok}")
  print_summary(results, args.dataset, args.K_max)


if __name__ == "__main__":
  main()
