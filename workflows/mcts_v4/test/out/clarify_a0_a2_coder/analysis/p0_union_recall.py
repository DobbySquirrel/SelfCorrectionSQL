#!/usr/bin/env python3
"""P0: static union recall ceiling across existing JSON pools (no GPU)."""
from __future__ import annotations

import json
import os
import signal
import sys
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

ROOT = Path(__file__).resolve().parents[6]
PAR = ROOT / "workflows/mcts_v4/test/out/clarify_a0_a2_coder/analysis/parallel_during_rerun"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(PAR))

import _loaders as pld  # noqa: E402

OUT_MD = Path(__file__).resolve().parent / "p0_union_recall.md"
OUT_JSON = Path(__file__).resolve().parent / "p0_union_recall.json"
EVAL_CACHE_PATH = Path(__file__).resolve().parent / "p0_eval_cache.json"

CALIB_498 = pld.OUT_BASE / "v4_calib_498q_coder_rollouts8.json"
CALIB_S7 = pld.OUT_BASE / "v4_calib_s7_41_coder_rollouts8.json"
S7_QIDS = pld.OUT_BASE / "s7_41_qids.txt"

WORKERS = int(os.environ.get("P0_WORKERS", min(32, (os.cpu_count() or 8))))
EVAL_TIMEOUT = float(os.environ.get("P0_EVAL_TIMEOUT", "40"))


def iter_pool_sqls(rec: dict):
    for s in rec.get("all_sqls_with_attributes") or []:
        if s.get("is_correct"):
            yield "__IS_CORRECT__"
        sql = (s.get("sql") or "").strip()
        if sql:
            yield sql
    for rs in rec.get("rollout_stats") or []:
        sel = (rs.get("selected_sql") or "").strip()
        if sel:
            yield sel
        for info in rs.get("all_sql_variants") or []:
            sql = (info.get("sql") or "").strip()
            if sql:
                yield sql


def extract_pool_index(rec: dict, qid: str) -> Tuple[bool, Set[str]]:
    """(fast_is_correct, sql_set) for one record."""
    if not rec:
        return False, set()
    sqls: Set[str] = set()
    for item in iter_pool_sqls(rec):
        if item == "__IS_CORRECT__":
            return True, set()
        sqls.add(item)
    return False, sqls


def _compare_with_timeout(sql: str, gs: str, conn, timeout: float) -> bool:
    """Per-SQL wall clock limit; timeout → False (fail)."""
    from workflows.mcts_v1.test.test_mcts import compare_with_gold

    if timeout <= 0 or not hasattr(signal, "SIGALRM"):
        return bool(compare_with_gold(sql, gs, conn))

    def _on_alarm(signum, frame):
        raise TimeoutError("compare_with_gold")

    secs = max(1, int(timeout))
    old = signal.signal(signal.SIGALRM, _on_alarm)
    signal.alarm(secs)
    try:
        return bool(compare_with_gold(sql, gs, conn))
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old)


def _eval_qid_batch(
    task: Tuple[str, List[str], str, str],
) -> Tuple[str, List[Tuple[str, str, bool]], int]:
    """Worker: one DB connection per qid; returns (qid, results, n_timeout)."""
    qid, sqls, gs, db = task
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    out: List[Tuple[str, str, bool]] = []
    n_timeout = 0
    if not gs or not db:
        return qid, [(qid, s, False) for s in sqls], 0
    try:
        from workflows.mcts_v1.test.test_mcts import build_db_connector

        conn = build_db_connector(db)
        try:
            for sql in sqls:
                if not sql:
                    out.append((qid, sql, False))
                    continue
                try:
                    ok = _compare_with_timeout(sql, gs, conn, EVAL_TIMEOUT)
                except TimeoutError:
                    ok = False
                    n_timeout += 1
                except Exception:
                    ok = False
                out.append((qid, sql, ok))
        finally:
            conn.disconnect()
    except Exception:
        out = [(qid, s, False) for s in sqls]
    return qid, out, n_timeout


def _tqdm_bar(iterable, total: int, desc: str):
    try:
        from tqdm import tqdm

        return tqdm(
            iterable,
            total=total,
            desc=desc,
            unit="qid",
            dynamic_ncols=True,
            file=sys.stderr,
            mininterval=0.3,
        )
    except ImportError:
        return _ascii_bar(iterable, total, desc)


def _ascii_bar(iterable, total: int, desc: str):
    done = 0
    width = 40
    for item in iterable:
        done += 1
        pct = done / max(total, 1)
        filled = int(width * pct)
        bar = "#" * filled + "-" * (width - filled)
        sys.stderr.write(
            f"\r{desc} [{bar}] {done}/{total} ({100*pct:.0f}%)   "
        )
        sys.stderr.flush()
        yield item
    sys.stderr.write("\n")


def build_global_eval_cache(
    pools: Dict[str, dict],
    qids: List[str],
    gold_sqls: dict,
    qid_to_db: dict,
) -> Tuple[dict, Dict[str, Dict[str, Tuple[bool, Set[str]]]]]:
    """
    Returns:
      eval_cache: (qid, sql) -> bool
      pool_index: pool_name -> qid -> (fast_correct, sql_set)
    """
    pool_index: Dict[str, Dict[str, Tuple[bool, Set[str]]]] = {}
    sqls_by_qid: Dict[str, Set[str]] = defaultdict(set)

    for pname, data in pools.items():
        pool_index[pname] = {}
        for qid in qids:
            fast, sqls = extract_pool_index(data.get(qid) or {}, qid)
            pool_index[pname][qid] = (fast, sqls)
            if not fast:
                sqls_by_qid[qid].update(sqls)

    batch_tasks: List[Tuple[str, List[str], str, str]] = []
    n_sql = 0
    for qid in qids:
        sqls = sorted(sqls_by_qid.get(qid) or [])
        if not sqls:
            continue
        n_sql += len(sqls)
        batch_tasks.append(
            (qid, sqls, gold_sqls.get(qid, ""), qid_to_db.get(qid, ""))
        )

    n_batch = len(batch_tasks)
    print(
        f"[P0] {n_sql} unique sql, {n_batch} qid-batches, "
        f"workers={WORKERS}, timeout={EVAL_TIMEOUT}s/sql"
    )
    eval_cache: dict = {}
    if not batch_tasks:
        return eval_cache, pool_index

    t0 = datetime.now()
    timeouts = 0
    hits = 0
    fut_timeout = EVAL_TIMEOUT + 5.0

    with ProcessPoolExecutor(max_workers=WORKERS) as ex:
        futs = {ex.submit(_eval_qid_batch, t): t for t in batch_tasks}
        bar = _tqdm_bar(as_completed(futs), total=n_batch, desc="P0 eval")
        for i, fut in enumerate(bar, 1):
            task = futs[fut]
            n_sql_q = len(task[1])
            try:
                _qid, rows, n_to = fut.result(
                    timeout=max(fut_timeout, EVAL_TIMEOUT * n_sql_q)
                )
            except Exception:
                _qid = task[0]
                rows = [(_qid, s, False) for s in task[1]]
                n_to = n_sql_q
                timeouts += 1
            timeouts += n_to
            for qid, sql, ok in rows:
                eval_cache[(qid, sql)] = ok
            hits = sum(1 for v in eval_cache.values() if v)
            elapsed = (datetime.now() - t0).total_seconds()
            rate = i / elapsed if elapsed > 0 else 0
            eta = (n_batch - i) / rate if rate > 0 else 0
            postfix = dict(sql_ok=hits, to=timeouts, eta=f"{eta:.0f}s")
            if hasattr(bar, "set_postfix"):
                bar.set_postfix(**postfix, refresh=True)
            elif i % max(1, n_batch // 20) == 0 or i == n_batch:
                sys.stderr.write(
                    f"  [{i}/{n_batch}] hits={hits} timeout={timeouts} eta={eta:.0f}s\n"
                )

    elapsed = (datetime.now() - t0).total_seconds()
    print(
        f"[P0] eval done {n_batch}/{n_batch} in {elapsed:.0f}s  "
        f"timeouts={timeouts}  sql_hits={hits}"
    )

    return eval_cache, pool_index


def recall_from_index(
    pool_index: Dict[str, Tuple[bool, Set[str]]],
    qids: List[str],
    eval_cache: dict,
) -> Dict[str, bool]:
    out: Dict[str, bool] = {}
    for qid in qids:
        fast, sqls = pool_index.get(qid, (False, set()))
        if fast:
            out[qid] = True
            continue
        out[qid] = any(eval_cache.get((qid, sql), False) for sql in sqls)
    return out


def union_map(maps: List[Dict[str, bool]], qids: List[str]) -> Dict[str, bool]:
    return {q: any(m.get(q, False) for m in maps) for q in qids}


def load_ef2_pool(qids: List[str]) -> dict:
    """ef2 single-run: only 51 ef2 rerun records; other qids empty."""
    ef2_qids = pld.load_ef2()
    if not pld.EF2_RERUN.exists():
        return {}
    rerun = pld.load_json(pld.EF2_RERUN)
    return {q: rerun[q] for q in qids if q in ef2_qids and q in rerun}


def only_exclusive(
    target: Dict[str, bool], others: List[Dict[str, bool]], qids: List[str]
) -> List[str]:
    return sorted(
        [q for q in qids if target.get(q) and not any(m.get(q) for m in others)],
        key=int,
    )


def overlap_matrix(
    r_final: Dict[str, bool],
    r_ef2: Dict[str, bool],
    r_calib: Dict[str, bool],
    qids: List[str],
) -> List[Tuple[str, int]]:
    """8-way recall presence: final / ef2 / calib."""
    rows: List[Tuple[str, int]] = []
    for f in (0, 1):
        for e in (0, 1):
            for c in (0, 1):
                label = f"final{'+' if f else '-'}/ef2{'+' if e else '-'}/calib{'+' if c else '-'}"
                n = sum(
                    1
                    for q in qids
                    if bool(r_final.get(q)) == bool(f)
                    and bool(r_ef2.get(q)) == bool(e)
                    and bool(r_calib.get(q)) == bool(c)
                )
                rows.append((label, n))
    return rows


def _fmt_recall(m: Dict[str, bool], qids: List[str]) -> str:
    n = len(qids)
    c = sum(1 for q in qids if m.get(q))
    return f"**{c}/{n}** ({100 * c / n:.1f}%)"


def write_report(
    qids: List[str],
    r_final: Dict[str, bool],
    r_ef2: Dict[str, bool],
    r_calib: Dict[str, bool],
    u_fe: Dict[str, bool],
    u_fc: Dict[str, bool],
    u_ec: Dict[str, bool],
    u_fec: Dict[str, bool],
    calib_only: List[str],
    final_only: List[str],
    ef2_only: List[str],
    missed_all: List[str],
    matrix: List[Tuple[str, int]],
    meta: dict,
) -> None:
    n = len(qids)
    u_fec_n = sum(1 for q in qids if u_fec.get(q))

    lines = [
        "# P0 — Union Recall Ceiling (static, no GPU)",
        "",
        f"Generated: {datetime.now().isoformat(timespec='seconds')}",
        f"Eval: {meta.get('unique_sql_evals', '?')} unique (qid,sql), "
        f"workers={meta.get('workers', '?')}, "
        f"timeout={meta.get('timeout', '?')}s/sql, {meta.get('elapsed_sec', 0):.0f}s",
        "",
        "## 1. Pool & Union Recall（最低输出表）",
        "",
        "| Pool / Union | Recall |",
        "|---|---:|",
        f"| final single-run | {_fmt_recall(r_final, qids)} |",
        f"| ef2 single-run (51q rerun only) | {_fmt_recall(r_ef2, qids)} |",
        f"| **final ∪ ef2** | {_fmt_recall(u_fe, qids)} |",
        f"| calib single-run | {_fmt_recall(r_calib, qids)} |",
        f"| final ∪ calib | {_fmt_recall(u_fc, qids)} |",
        f"| ef2 ∪ calib | {_fmt_recall(u_ec, qids)} |",
        f"| **final ∪ ef2 ∪ calib** | {_fmt_recall(u_fec, qids)} |",
        "",
        "> ef2 single-run：仅 `v4_ef2_51_rerun` 的 51 题有池；其余 qid 无记录 → recall 为 false。",
        "> final ∪ ef2 应等于 overlay 后的 merged final+ef2 口径。",
        "",
        "## 2. Exclusive qids",
        "",
        f"| 集合 | n | qids |",
        f"|---|---:|---|",
        f"| **calib_only**（calib ✓, final ✗, ef2 ✗） | {len(calib_only)} | `{', '.join(calib_only)}` |",
        f"| **final_only**（final ✓, calib ✗, ef2 ✗） | {len(final_only)} | `{', '.join(final_only)}` |",
        f"| **ef2_only**（ef2 ✓, final ✗, calib ✗） | {len(ef2_only)} | `{', '.join(ef2_only)}` |",
        f"| **missed_by_all** | {len(missed_all)} | `{', '.join(missed_all[:60])}"
        + ("…" if len(missed_all) > 60 else "")
        + "` |",
        "",
        "## 3. Overlap matrix（recall 在池内）",
        "",
        "| final | ef2 | calib | n |",
        "|---|---|---|---:|",
    ]
    for label, cnt in matrix:
        parts = label.split("/")
        f = "✓" if parts[0].endswith("+") else "✗"
        e = "✓" if parts[1].endswith("+") else "✗"
        c = "✓" if parts[2].endswith("+") else "✗"
        lines.append(f"| {f} | {e} | {c} | {cnt} |")

    lines += ["", "## 4. 决策（final ∪ ef2 ∪ calib）", ""]
    if u_fec_n >= 440:
        verdict = f"✅ **{u_fec_n}/498 ≥ 440** → calibration/novelty 很值得继续（方案 B merge pool）。"
    elif u_fec_n >= 430:
        verdict = f"⚠️ **{u_fec_n}/498** 在 430–439 → 中等；adaptive extra rollout 仍有希望。"
    else:
        verdict = (
            f"🛑 **{u_fec_n}/498 ≤ 429** → 现有搜索空间互补性弱；"
            "优先 clarify constraint / regenerate，而非单纯加 r。"
        )
    lines.append(verdict)
    lines.append("")
    lines.append("静态 pool union；不重跑 GPU。")

    OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")


def recall_via_batch(data: dict, qids: List[str], gold_sqls: dict, qid_to_db: dict) -> Dict[str, bool]:
    from workflows.mcts_v4.scripts.cost_quality_analysis import compute_recall_batch

    sub = {q: data[q] for q in qids if q in data}
    return compute_recall_batch(sub, gold_sqls, qid_to_db, qids=qids)


def main() -> None:
    gold_sqls, qid_to_db = pld.load_gold_meta()
    final = pld.load_json(pld.FINAL_PATH)
    merged = pld.load_merged_498()
    calib = pld.load_json(CALIB_498) if CALIB_498.exists() else {}

    qids = sorted({str(k) for k in calib or final or merged}, key=int)
    n = len(qids)

    ef2_only_data = load_ef2_pool(qids)
    ef2_sparse = {q: (ef2_only_data.get(q) or {}) for q in qids}

    t0 = datetime.now()
    use_batch = os.environ.get("P0_USE_BATCH", "1") == "1"
    use_cache = os.environ.get("P0_REPORT_ONLY", "0") == "1" and OUT_JSON.exists()

    if use_cache:
        prev = json.loads(OUT_JSON.read_text(encoding="utf-8"))
        rq = prev.get("recall_qid") or {}
        r_final = {q: rq[q]["final"] for q in qids if q in rq}
        r_ef2 = {q: rq[q]["ef2"] for q in qids if q in rq}
        r_calib = {q: rq[q]["calib"] for q in qids if q in rq}
        print(f"[P0] report-only from {OUT_JSON.name}")
        elapsed = 0.0
        meta = {k: prev.get(k) for k in ("workers", "unique_sql_evals", "elapsed_sec")}
        meta["timeout"] = EVAL_TIMEOUT
    elif use_batch:
        print(f"[P0] {n} qids, batch recall (final / ef2-51 / calib)...")
        r_final = recall_via_batch(final, qids, gold_sqls, qid_to_db)
        r_ef2 = recall_via_batch(ef2_sparse, qids, gold_sqls, qid_to_db)
        r_calib = recall_via_batch(calib, qids, gold_sqls, qid_to_db)
        elapsed = (datetime.now() - t0).total_seconds()
        meta = {"workers": "batch", "timeout": 0, "unique_sql_evals": 0, "elapsed_sec": elapsed}
    else:
        pools = {"final": final, "ef2": ef2_only_data, "calib": calib}
        print(f"[P0] {n} qids, parallel sql eval (final / ef2-51 / calib)...")
        eval_cache, pool_index = build_global_eval_cache(pools, qids, gold_sqls, qid_to_db)
        r_final = recall_from_index(pool_index["final"], qids, eval_cache)
        r_ef2 = recall_from_index(pool_index["ef2"], qids, eval_cache)
        r_calib = recall_from_index(pool_index["calib"], qids, eval_cache)
        elapsed = (datetime.now() - t0).total_seconds()
        meta = {
            "workers": WORKERS,
            "timeout": EVAL_TIMEOUT,
            "unique_sql_evals": len(eval_cache),
            "elapsed_sec": elapsed,
        }
        EVAL_CACHE_PATH.write_text(
            json.dumps({f"{q}|||{sql}": v for (q, sql), v in eval_cache.items()}),
            encoding="utf-8",
        )

    u_fe = union_map([r_final, r_ef2], qids)
    u_fc = union_map([r_final, r_calib], qids)
    u_ec = union_map([r_ef2, r_calib], qids)
    u_fec = union_map([r_final, r_ef2, r_calib], qids)

    r_merged = recall_via_batch(merged, qids, gold_sqls, qid_to_db) if use_batch else {}

    calib_only = only_exclusive(r_calib, [r_final, r_ef2], qids)
    final_only = only_exclusive(r_final, [r_calib, r_ef2], qids)
    ef2_only = only_exclusive(r_ef2, [r_final, r_calib], qids)
    missed_all = sorted(
        [q for q in qids if not r_final.get(q) and not r_ef2.get(q) and not r_calib.get(q)],
        key=int,
    )
    matrix = overlap_matrix(r_final, r_ef2, r_calib, qids)

    if not use_cache:
        print(f"[P0] done in {elapsed:.0f}s")

    def cnt(m: Dict[str, bool]) -> int:
        return sum(1 for q in qids if m.get(q))

    write_report(
        qids,
        r_final,
        r_ef2,
        r_calib,
        u_fe,
        u_fc,
        u_ec,
        u_fec,
        calib_only,
        final_only,
        ef2_only,
        missed_all,
        matrix,
        meta,
    )

    payload = {
        "n": n,
        **meta,
        "single": {
            "final": cnt(r_final),
            "ef2_51_only": cnt(r_ef2),
            "calib": cnt(r_calib),
            "merged_overlay_check": cnt(r_merged),
        },
        "union": {
            "final_ef2": cnt(u_fe),
            "final_calib": cnt(u_fc),
            "ef2_calib": cnt(u_ec),
            "final_ef2_calib": cnt(u_fec),
        },
        "exclusive": {
            "calib_only": calib_only,
            "final_only": final_only,
            "ef2_only": ef2_only,
            "missed_by_all": missed_all,
        },
        "overlap_matrix": {label: n for label, n in matrix},
        "recall_qid": {
            q: {
                "final": r_final.get(q, False),
                "ef2": r_ef2.get(q, False),
                "calib": r_calib.get(q, False),
            }
            for q in qids
        },
    }
    OUT_JSON.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(OUT_MD)
    print(
        f"final={cnt(r_final)} ef2={cnt(r_ef2)} calib={cnt(r_calib)} "
        f"union_fec={cnt(u_fec)} merged_check={cnt(r_merged)}"
    )


if __name__ == "__main__":
    main()
