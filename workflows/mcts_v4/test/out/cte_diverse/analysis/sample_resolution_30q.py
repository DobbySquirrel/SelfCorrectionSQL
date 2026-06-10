#!/usr/bin/env python3
"""从 B′/B″/union 498 结果分层抽取 30q（最大化实验分辨率，非代表全集）。"""

from __future__ import annotations

import hashlib
import io
import json
import random
import sys
from contextlib import redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

MCTS_V4 = Path(__file__).resolve().parents[4]
PAR = MCTS_V4 / "test/out/clarify_a0_a2_coder/analysis/parallel_during_rerun"
sys.path.insert(0, str(MCTS_V4))
sys.path.insert(0, str(PAR))

from utils.sql_selector import SQLSelector  # noqa: E402
import selector_replay as sr  # noqa: E402
from selector_replay import build_clusters, _high_reward_count  # noqa: E402

OUT = Path(__file__).resolve().parent.parent
PREV_30 = MCTS_V4 / "test/out/clarify_a0_a2_qwen32/qids_30_manifest.json"
S7_QIDS = MCTS_V4 / "test/out/clarify_a0_a2_coder/s7_41_qids.txt"

SEED = 20260609
K = {"A": 10, "B": 10, "C": 5, "D": 5}

OUT_JSON = OUT / "qids_resolution_30.json"
OUT_TXT = OUT / "qids_resolution_30.txt"
OUT_MD = Path(__file__).resolve().parent / "qids_resolution_30_sampling.md"


def load_shards(prefix: str, n: int = 4) -> Dict[str, Any]:
    data: Dict[str, Any] = {}
    for i in range(n):
        p = OUT / f"{prefix}_w{i}.json"
        data.update(json.loads(p.read_text(encoding="utf-8")))
    return data


def has_recall(rec: Dict[str, Any]) -> bool:
    return any(a.get("is_correct") for a in (rec.get("all_sqls_with_attributes") or []))


def r3_pick(rec: Dict[str, Any]) -> str:
    with redirect_stdout(io.StringIO()):
        return (SQLSelector.select(rec.get("rollout_stats") or [], strategy="R3") or "").strip()


def r3_hit(rec: Dict[str, Any]) -> bool:
    sm = {
        (a.get("sql") or "").strip(): bool(a.get("is_correct"))
        for a in (rec.get("all_sqls_with_attributes") or [])
    }
    return bool(sm.get(r3_pick(rec)))


def is_anomaly(rec: Dict[str, Any], min_rollouts: int) -> Tuple[bool, str]:
    stats = rec.get("stats") or {}
    if stats.get("task_timeout") or stats.get("timeout_fallback_failed"):
        return True, "timeout"
    rss = rec.get("rollout_stats") or []
    if len(rss) < min_rollouts:
        return True, f"rollouts<{min_rollouts}"
    if not rss and not (rec.get("all_sqls_with_attributes") or []):
        return True, "empty_record"
    return False, ""


def gold_sig_set(rec: Dict[str, Any]) -> Set[str]:
    sm = {
        (a.get("sql") or "").strip(): bool(a.get("is_correct"))
        for a in (rec.get("all_sqls_with_attributes") or [])
    }
    out: Set[str] = set()
    for rs in rec.get("rollout_stats") or []:
        for v in rs.get("all_sql_variants") or []:
            sql = (v.get("sql") or "").strip()
            sig = v.get("result_signature") or ""
            if sql and sig and sm.get(sql):
                out.add(sig)
    return out


def false_consensus_score(rec: Dict[str, Any]) -> float:
    """S7-like：高 reward 多 + 错簇 visit 强 + R3 选错。"""
    rss = rec.get("rollout_stats") or []
    if not rss:
        return -1.0
    clusters = build_clusters(rss)
    if len(clusters) < 2:
        return -1.0
    hi = _high_reward_count(rss)
    if hi < 4:
        return -1.0
    ranked = sorted(clusters.items(), key=lambda x: (-x[1].total_visit, -x[1].total_count))
    top_sig, top_c = ranked[0]
    second_visit = ranked[1][1].total_visit if len(ranked) > 1 else 0
    gold_sigs = gold_sig_set(rec)
    top_has_gold = top_sig in gold_sigs
    visit_ratio = (top_c.total_visit / second_visit) if second_visit > 0 else 99.0
    r3_ok = r3_hit(rec)
    score = 0.0
    score += min(hi, 12) * 2.0
    score += min(visit_ratio, 20.0)
    score += top_c.total_count * 0.5
    score += top_c.max_rollout_reward * 5.0
    if not top_has_gold:
        score += 15.0
    if not r3_ok:
        score += 10.0
    if has_recall(rec):
        score += 5.0  # 有 gold 在池但被困错簇
    return score


def build_profiles(b2: Dict[str, Any], b2pp: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    prev30 = set(json.loads(PREV_30.read_text(encoding="utf-8"))["qids"])
    s7_set = set(ln.strip() for ln in S7_QIDS.read_text().splitlines() if ln.strip()) if S7_QIDS.is_file() else set()

    qids = sorted(set(b2) & set(b2pp), key=int)
    prof: Dict[str, Dict[str, Any]] = {}

    for qid in qids:
        rb, rp = b2[qid], b2pp[qid]
        an_b, why_b = is_anomaly(rb, 10)
        an_p, why_p = is_anomaly(rp, 12)
        rec_b = has_recall(rb)
        rec_p = has_recall(rp)
        rec_u = rec_b or rec_p
        hit_b = r3_hit(rb)
        hit_p = r3_hit(rp)
        merged = (rb.get("rollout_stats") or []) + (rp.get("rollout_stats") or [])
        with redirect_stdout(io.StringIO()):
            pick_u = (SQLSelector.select(merged, strategy="R3") or "").strip()
        sm_u = {
            (a.get("sql") or "").strip(): bool(a.get("is_correct"))
            for a in (rb.get("all_sqls_with_attributes") or []) + (rp.get("all_sqls_with_attributes") or [])
        }
        hit_u = bool(sm_u.get(pick_u))

        exclude_reason = None
        if qid in prev30:
            exclude_reason = "prev_30q"
        elif hit_b and hit_p:
            exclude_reason = "both_stable_hit"
        elif an_b or an_p:
            exclude_reason = f"anomaly:{why_b or why_p}"

        prof[qid] = {
            "rec_b2": rec_b,
            "rec_b2pp": rec_p,
            "rec_union": rec_u,
            "hit_b2": hit_b,
            "hit_b2pp": hit_p,
            "hit_union": hit_u,
            "in_s7_41": qid in s7_set,
            "fc_score_b2": false_consensus_score(rb),
            "fc_score_b2pp": false_consensus_score(rp),
            "exclude": exclude_reason,
        }
    return prof


def eligible(prof: Dict[str, Dict], qid: str) -> bool:
    return prof[qid]["exclude"] is None


def pick_top(pool: List[str], prof: Dict[str, Dict], key_fn, k: int, rng: random.Random) -> List[str]:
    if len(pool) <= k:
        return sorted(pool, key=int)
    scored = [(key_fn(qid), qid) for qid in pool]
    scored.sort(key=lambda x: (-x[0], int(x[1])))
    # 取得分最高的 2k，再随机抽 k（避免全取极端同一类）
    head = [q for _, q in scored[: min(len(scored), k * 2)]]
    return sorted(rng.sample(head, k), key=int)


def sample_buckets(prof: Dict[str, Dict]) -> Dict[str, List[str]]:
    rng = random.Random(SEED)
    used: Set[str] = set()
    buckets: Dict[str, List[str]] = {}

    def take(name: str, qids: List[str]) -> None:
        overlap = set(qids) & used
        if overlap:
            raise RuntimeError(f"{name} overlaps: {overlap}")
        buckets[name] = qids
        used.update(qids)

    # A: B′ no recall, union has recall
    pool_a = [
        q for q, p in prof.items()
        if eligible(prof, q) and not p["rec_b2"] and p["rec_union"]
    ]

    def score_a(qid: str) -> float:
        p = prof[qid]
        s = 0.0
        if p["rec_b2pp"] and not p["rec_b2"]:
            s += 20.0  # 纯 B″ 补搜索
        if p["hit_b2pp"]:
            s += 8.0
        if not p["hit_b2pp"] and p["rec_b2pp"]:
            s += 3.0  # 有 recall 但选题仍难
        return s

    take("A_search_miss_other_hits", pick_top(pool_a, prof, score_a, K["A"], rng))

    # B: B′ recall but R3 miss
    pool_b = [
        q for q, p in prof.items()
        if eligible(prof, q) and p["rec_b2"] and not p["hit_b2"] and q not in used
    ]

    def score_b(qid: str) -> float:
        p = prof[qid]
        s = 0.0
        if p["hit_b2pp"]:
            s += 12.0  # 另一侧能选对 → selector 可修复信号强
        if p["rec_b2pp"]:
            s += 4.0
        s += max(prof[qid]["fc_score_b2"], 0) * 0.3
        return s

    take("B_selection_miss", pick_top(pool_b, prof, score_b, K["B"], rng))

    # C: all no recall
    pool_c = [
        q for q, p in prof.items()
        if eligible(prof, q) and not p["rec_union"] and q not in used
    ]

    def score_c(qid: str) -> float:
        p = prof[qid]
        rb = b2_global[qid]
        hi = _high_reward_count(rb.get("rollout_stats") or [])
        return hi + max(p["fc_score_b2"], p["fc_score_b2pp"]) * 0.1

    take("C_both_no_recall", pick_top(pool_c, prof, score_c, K["C"], rng))

    # D: false-consensus / S7-like
    pool_d = [
        q for q, p in prof.items()
        if eligible(prof, q) and q not in used
        and max(p["fc_score_b2"], p["fc_score_b2pp"]) >= 25.0
    ]

    def score_d(qid: str) -> float:
        p = prof[qid]
        s = max(p["fc_score_b2"], p["fc_score_b2pp"])
        if p["in_s7_41"]:
            s += 8.0
        if p["rec_b2"] and not p["hit_b2"]:
            s += 5.0
        if not p["rec_b2"]:
            s += 2.0
        return s

    take("D_false_consensus", pick_top(pool_d, prof, score_d, K["D"], rng))

    all_q = sorted(used, key=int)
    if len(all_q) != 30:
        raise RuntimeError(f"expected 30 qids, got {len(all_q)}; buckets={{{', '.join(f'{k}:{len(v)}' for k,v in buckets.items())}}}")

    return buckets


def sha256_qids(qids: List[str]) -> str:
    payload = "\n".join(sorted(qids, key=int)) + "\n"
    return hashlib.sha256(payload.encode()).hexdigest()


def write_md(buckets: Dict[str, List[str]], prof: Dict[str, Dict], pool_sizes: Dict[str, int]) -> None:
    lines = [
        "# Resolution 30q sampling (B′/B″/union)",
        "",
        f"Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}",
        f"Seed: `{SEED}` | SHA256: `{sha256_qids(sorted({q for qs in buckets.values() for q in qs}, key=int))}`",
        "",
        "## 目标",
        "",
        "非代表全集，而是最大化实验分辨率：测 subquery chart 能否解决原系统真正卡住的地方。",
        "",
        "## 排除",
        "",
        "- 旧 30q (`qids_30_manifest.json`)",
        "- B′/B″ R3 都命中（稳定对）",
        "- 落盘/timeout 异常（rollout 过少或 task_timeout）",
        "",
        "## 桶定义",
        "",
        "| 桶 | k | 定义 | 测什么 |",
        "|---|---:|---|---|",
        "| A | 10 | B′ no recall，union 有 recall | subquery chart 是否补搜索空间 |",
        "| B | 10 | B′ 有 recall，R3 未选中 | chart 是否改善 cluster support / selector |",
        "| C | 5 | B′/B″/union 都无 recall | 真正 hard search ceiling |",
        "| D | 5 | 高 reward + 错簇强 (S7-like) | chart 是否打破错误分解先验 |",
        "",
        "## 候选池大小（排除后）",
        "",
    ]
    for k, v in pool_sizes.items():
        lines.append(f"- **{k}**: {v}")
    lines.extend(["", "## 选中题目", ""])

    desc = {
        "A_search_miss_other_hits": "A · search-miss, other hits",
        "B_selection_miss": "B · selection-miss",
        "C_both_no_recall": "C · both no recall",
        "D_false_consensus": "D · false-consensus",
    }
    for bucket, qids in buckets.items():
        lines.append(f"### {desc.get(bucket, bucket)} ({len(qids)})")
        lines.append("")
        lines.append("| qid | B′rec | B″rec | B′hit | B″hit | union hit | S7 | fc_score |")
        lines.append("|---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|")
        for qid in qids:
            p = prof[qid]
            fc = max(p["fc_score_b2"], p["fc_score_b2pp"])
            lines.append(
                f"| {qid} | {int(p['rec_b2'])} | {int(p['rec_b2pp'])} | {int(p['hit_b2'])} | "
                f"{int(p['hit_b2pp'])} | {int(p['hit_union'])} | {int(p['in_s7_41'])} | {fc:.1f} |"
            )
        lines.append("")

    lines.append("## QID list")
    lines.append("")
    lines.append("```")
    lines.extend(sorted({q for qs in buckets.values() for q in qs}, key=int))
    lines.append("```")
    OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")


b2_global: Dict[str, Any] = {}
b2pp_global: Dict[str, Any] = {}


def main() -> None:
    global b2_global, b2pp_global
    b2_global = load_shards("v4_diverse_b2_n3_sv5_498q_coder_rollouts12")
    b2pp_global = load_shards("v4_diverse_b2pp_n3_sv3_498q_coder_rollouts15")
    prof = build_profiles(b2_global, b2pp_global)

    pool_sizes = {
        "A": len([q for q, p in prof.items() if eligible(prof, q) and not p["rec_b2"] and p["rec_union"]]),
        "B": len([q for q, p in prof.items() if eligible(prof, q) and p["rec_b2"] and not p["hit_b2"]]),
        "C": len([q for q, p in prof.items() if eligible(prof, q) and not p["rec_union"]]),
        "D": len([
            q for q, p in prof.items()
            if eligible(prof, q) and max(p["fc_score_b2"], p["fc_score_b2pp"]) >= 25.0
        ]),
    }

    buckets = sample_buckets(prof)
    all_qids = sorted({q for qs in buckets.values() for q in qs}, key=int)

    OUT_JSON.write_text(
        json.dumps(
            {
                "seed": SEED,
                "sha256": sha256_qids(all_qids),
                "exclude": {
                    "prev_30q": json.loads(PREV_30.read_text())["qids"],
                    "both_stable_hit": True,
                    "anomaly": True,
                },
                "buckets": buckets,
                "profiles": {q: prof[q] for q in all_qids},
                "pool_sizes_eligible": pool_sizes,
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    OUT_TXT.write_text("\n".join(all_qids) + "\n", encoding="utf-8")
    write_md(buckets, prof, pool_sizes)

    print("Pool sizes (eligible):", pool_sizes)
    for name, qs in buckets.items():
        print(f"  {name}: {len(qs)} -> {qs}")
    print(f"Total: {len(all_qids)}")
    print(f"Wrote {OUT_JSON}")
    print(f"Wrote {OUT_TXT}")
    print(f"Wrote {OUT_MD}")


if __name__ == "__main__":
    main()
