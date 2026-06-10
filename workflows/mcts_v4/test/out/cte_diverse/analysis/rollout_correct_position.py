#!/usr/bin/env python3
"""统计正确 SQL 出现在第几个 rollout，并画图。"""

from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import matplotlib.pyplot as plt
import numpy as np

MCTS_V4 = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(MCTS_V4))

from utils.sql_selector import SQLSelector  # noqa: E402

OUT = Path(__file__).resolve().parent.parent
FIG_DIR = OUT / "figures"
FIG_DIR.mkdir(parents=True, exist_ok=True)


def load_shards(prefix: str, n_shards: int = 4) -> Dict[str, Any]:
    data: Dict[str, Any] = {}
    for i in range(n_shards):
        p = OUT / f"{prefix}_w{i}.json"
        if not p.exists():
            raise FileNotFoundError(p)
        data.update(json.loads(p.read_text()))
    return data


def sql_correct_map(rec: Dict[str, Any]) -> Dict[str, bool]:
    return {
        (a.get("sql") or "").strip(): bool(a.get("is_correct"))
        for a in (rec.get("all_sqls_with_attributes") or [])
        if (a.get("sql") or "").strip()
    }


def rollouts_with_correct(rec: Dict[str, Any]) -> List[int]:
    """返回含至少一条正确 SQL 的 rollout_id 列表（升序）。"""
    sm = sql_correct_map(rec)
    if not any(sm.values()):
        return []
    ids: List[int] = []
    for rs in rec.get("rollout_stats") or []:
        rid = int(rs.get("rollout_id") or 0)
        if rid <= 0:
            continue
        found = False
        sel = (rs.get("selected_sql") or "").strip()
        if sm.get(sel):
            found = True
        if not found:
            for v in rs.get("all_sql_variants") or []:
                if sm.get((v.get("sql") or "").strip()):
                    found = True
                    break
        if found:
            ids.append(rid)
    return sorted(ids)


def rollout_of_sql(rec: Dict[str, Any], sql: str) -> Optional[int]:
    """SQL 若出现在多个 rollout，取 reward 最高的那个 rollout_id。"""
    target = (sql or "").strip()
    if not target:
        return None
    best_rid: Optional[int] = None
    best_rw = -1.0
    for rs in rec.get("rollout_stats") or []:
        rid = int(rs.get("rollout_id") or 0)
        rw = float(rs.get("reward") or 0.0)
        candidates = [(rs.get("selected_sql") or "").strip()]
        candidates.extend((v.get("sql") or "").strip() for v in (rs.get("all_sql_variants") or []))
        if target in candidates and rw >= best_rw:
            best_rw = rw
            best_rid = rid
    return best_rid


def analyze_dataset(
    data: Dict[str, Any],
    label: str,
    max_rollout: int,
    strategy: str = "R3",
) -> Dict[str, Any]:
    qids = sorted(data.keys(), key=lambda x: int(x) if str(x).isdigit() else x)
    n_total = len(qids)

    first_correct: List[int] = []
    all_correct_sets: List[Set[int]] = []
    per_rollout_hits = Counter()
    cumulative_found = Counter()  # rollout k -> count with first_correct <= k
    r3_hit_rollouts: List[int] = []
    r3_miss_but_recall_first: List[int] = []

    n_recall = 0
    n_hit = 0

    for qid in qids:
        rec = data[qid]
        sm = sql_correct_map(rec)
        has_recall = any(sm.values())
        if has_recall:
            n_recall += 1

        correct_rollouts = rollouts_with_correct(rec)
        if correct_rollouts:
            fc = correct_rollouts[0]
            first_correct.append(fc)
            all_correct_sets.append(set(correct_rollouts))
            for rid in correct_rollouts:
                per_rollout_hits[rid] += 1
            for k in range(fc, max_rollout + 1):
                cumulative_found[k] += 1

        picked = SQLSelector.select(rec.get("rollout_stats") or [], strategy=strategy)
        picked_ok = bool(sm.get((picked or "").strip()))
        if picked_ok:
            n_hit += 1
            rid = rollout_of_sql(rec, picked)
            if rid is not None:
                r3_hit_rollouts.append(rid)
        elif has_recall:
            if correct_rollouts:
                r3_miss_but_recall_first.append(correct_rollouts[0])

    return {
        "label": label,
        "max_rollout": max_rollout,
        "n_total": n_total,
        "n_recall": n_recall,
        "n_hit": n_hit,
        "first_correct": first_correct,
        "per_rollout_hits": per_rollout_hits,
        "cumulative_found": cumulative_found,
        "r3_hit_rollouts": r3_hit_rollouts,
        "r3_miss_but_recall_first": r3_miss_but_recall_first,
        "all_correct_sets": all_correct_sets,
    }


def analyze_union(b2: Dict[str, Any], b2pp: Dict[str, Any], strategy: str = "R3") -> Dict[str, Any]:
    """合并两侧 rollout_stats 后统计 first correct / R3 hit rollout。"""
    qids = sorted(set(b2) & set(b2pp), key=lambda x: int(x) if str(x).isdigit() else x)
    max_rollout = 27  # 12 + 15, 用连续编号区分来源

    first_correct: List[int] = []
    per_source_first: Counter = Counter()  # 'B2' or 'B2pp'
    r3_hit_source: Counter = Counter()
    r3_hit_rollout_within_source: List[Tuple[str, int]] = []
    n_recall = 0
    n_hit = 0

    for qid in qids:
        rec_b2 = b2[qid]
        rec_pp = b2pp[qid]
        sm = sql_correct_map(rec_b2)
        sm.update(sql_correct_map(rec_pp))
        has_recall = any(sm.values())
        if has_recall:
            n_recall += 1

        # 带来源标记的 rollout 列表
        tagged: List[Tuple[str, int, Dict]] = []
        for rs in rec_b2.get("rollout_stats") or []:
            tagged.append(("B′", int(rs.get("rollout_id") or 0), rs))
        for rs in rec_pp.get("rollout_stats") or []:
            tagged.append(("B″", int(rs.get("rollout_id") or 0), rs))

        correct_tagged: List[Tuple[str, int]] = []
        for src, rid, rs in tagged:
            if rid <= 0:
                continue
            found = False
            sel = (rs.get("selected_sql") or "").strip()
            if sm.get(sel):
                found = True
            if not found:
                for v in rs.get("all_sql_variants") or []:
                    if sm.get((v.get("sql") or "").strip()):
                        found = True
                        break
            if found:
                correct_tagged.append((src, rid))

        if correct_tagged:
            # 按 B′ rollouts 先、再 B″，同侧按 rollout_id
            order = {("B′", i): i for i in range(1, 20)}
            for i in range(1, 20):
                order[("B″", i)] = 12 + i
            correct_tagged.sort(key=lambda x: order.get(x, 999))
            src0, rid0 = correct_tagged[0]
            first_correct.append(order[(src0, rid0)])
            per_source_first[src0] += 1

        merged_stats = (rec_b2.get("rollout_stats") or []) + (rec_pp.get("rollout_stats") or [])
        picked = SQLSelector.select(merged_stats, strategy=strategy)
        picked_ok = bool(sm.get((picked or "").strip()))
        if picked_ok:
            n_hit += 1
            # 判断来自哪一侧
            for src, rec in [("B′", rec_b2), ("B″", rec_pp)]:
                rid = rollout_of_sql(rec, picked)
                if rid is not None:
                    r3_hit_source[src] += 1
                    r3_hit_rollout_within_source.append((src, rid))
                    break

    return {
        "label": "Union (B′+B″)",
        "n_total": len(qids),
        "n_recall": n_recall,
        "n_hit": n_hit,
        "first_correct": first_correct,
        "per_source_first": per_source_first,
        "r3_hit_source": r3_hit_source,
        "r3_hit_rollout_within_source": r3_hit_rollout_within_source,
    }


def plot_first_correct_hist(stats_list: List[Dict[str, Any]], out_path: Path) -> None:
    fig, axes = plt.subplots(1, len(stats_list), figsize=(5 * len(stats_list), 4.5), sharey=True)
    if len(stats_list) == 1:
        axes = [axes]

    for ax, st in zip(axes, stats_list):
        max_r = st["max_rollout"]
        xs = list(range(1, max_r + 1))
        cnt = Counter(st["first_correct"])
        ys = [cnt.get(i, 0) for i in xs]
        bars = ax.bar(xs, ys, color="#4C78A8", edgecolor="white", linewidth=0.6)
        ax.set_title(f"{st['label']}\nfirst correct rollout (Recall={st['n_recall']})")
        ax.set_xlabel("rollout #")
        ax.set_xticks(xs)
        ax.grid(axis="y", alpha=0.3)
        for b, y in zip(bars, ys):
            if y:
                ax.text(b.get_x() + b.get_width() / 2, y + 0.5, str(y), ha="center", va="bottom", fontsize=8)

    axes[0].set_ylabel("# questions")
    fig.suptitle("正确 SQL 首次出现在第几个 rollout", fontsize=13, y=1.02)
    fig.tight_layout()
    fig.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close(fig)


def plot_cumulative(stats_list: List[Dict[str, Any]], out_path: Path) -> None:
    fig, ax = plt.subplots(figsize=(8, 5))
    colors = {"B′": "#4C78A8", "B″": "#F58518", "Union (B′+B″)": "#54A24B"}

    for st in stats_list:
        if "cumulative_found" not in st:
            continue
        max_r = st["max_rollout"]
        n_recall = st["n_recall"] or 1
        xs = list(range(1, max_r + 1))
        ys = [100.0 * st["cumulative_found"].get(k, 0) / n_recall for k in xs]
        ax.plot(xs, ys, marker="o", markersize=4, label=st["label"], color=colors.get(st["label"], None))

    ax.set_xlabel("rollout # (累计到该 rollout)")
    ax.set_ylabel("% Recall 题目已出现正确 SQL")
    ax.set_title("累计：前 k 个 rollout 内找到正确 SQL 的比例")
    ax.legend()
    ax.grid(alpha=0.3)
    ax.set_ylim(0, 105)
    fig.tight_layout()
    fig.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close(fig)


def plot_r3_hit_rollout(stats_list: List[Dict[str, Any]], out_path: Path) -> None:
    fig, axes = plt.subplots(1, len(stats_list), figsize=(5 * len(stats_list), 4.5), sharey=True)
    if len(stats_list) == 1:
        axes = [axes]

    for ax, st in zip(axes, stats_list):
        if "r3_hit_rollouts" not in st:
            ax.set_visible(False)
            continue
        max_r = st["max_rollout"]
        xs = list(range(1, max_r + 1))
        cnt = Counter(st["r3_hit_rollouts"])
        ys = [cnt.get(i, 0) for i in xs]
        ax.bar(xs, ys, color="#54A24B", edgecolor="white", linewidth=0.6)
        ax.set_title(f"{st['label']}\nR3 Hit@1 正确时 SQL 来自 rollout # (n={st['n_hit']})")
        ax.set_xlabel("rollout #")
        ax.set_xticks(xs)
        ax.grid(axis="y", alpha=0.3)

    axes[0].set_ylabel("# questions")
    fig.suptitle("R3 选中且正确的 SQL 来自哪个 rollout", fontsize=13, y=1.02)
    fig.tight_layout()
    fig.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close(fig)


def print_summary(st: Dict[str, Any]) -> None:
    print(f"\n=== {st['label']} ===")
    print(f"总题数: {st['n_total']}, Recall: {st['n_recall']}, R3 Hit@1: {st['n_hit']}")
    if st.get("first_correct"):
        fc = st["first_correct"]
        cnt = Counter(fc)
        print("首次正确 rollout 分布:")
        for k in sorted(cnt):
            print(f"  rollout {k}: {cnt[k]} ({100*cnt[k]/len(fc):.1f}%)")
        print(f"  均值: {np.mean(fc):.2f}, 中位数: {np.median(fc):.1f}")
    if st.get("r3_hit_rollouts"):
        cnt = Counter(st["r3_hit_rollouts"])
        print("R3 正确时来源 rollout:")
        for k in sorted(cnt):
            print(f"  rollout {k}: {cnt[k]} ({100*cnt[k]/st['n_hit']:.1f}%)")
        print(f"  均值: {np.mean(st['r3_hit_rollouts']):.2f}")
    if st.get("per_source_first"):
        print("并集 Recall 首次正确来源:", dict(st["per_source_first"]))
    if st.get("r3_hit_source"):
        print("并集 R3 正确来源侧:", dict(st["r3_hit_source"]))


def main() -> None:
    b2 = load_shards("v4_diverse_b2_n3_sv5_498q_coder_rollouts12")
    b2pp = load_shards("v4_diverse_b2pp_n3_sv3_498q_coder_rollouts15")

    st_b2 = analyze_dataset(b2, "B′", max_rollout=12, strategy="R3")
    st_pp = analyze_dataset(b2pp, "B″", max_rollout=15, strategy="R3")
    st_union = analyze_union(b2, b2pp, strategy="R3")

    for st in [st_b2, st_pp, st_union]:
        print_summary(st)

    plot_first_correct_hist(
        [st_b2, st_pp],
        FIG_DIR / "rollout_first_correct_hist_b2_b2pp.png",
    )
    plot_cumulative(
        [st_b2, st_pp],
        FIG_DIR / "rollout_cumulative_recall_b2_b2pp.png",
    )
    plot_r3_hit_rollout(
        [st_b2, st_pp],
        FIG_DIR / "rollout_r3_hit_source_b2_b2pp.png",
    )

    # 并集：首次正确来自哪一侧
    fig, ax = plt.subplots(figsize=(5, 4))
    src_cnt = st_union["per_source_first"]
    labels = list(src_cnt.keys())
    vals = [src_cnt[l] for l in labels]
    ax.bar(labels, vals, color=["#4C78A8", "#F58518"])
    ax.set_title(f"并集 Recall：首次正确 SQL 来自哪一侧\n(n={sum(vals)})")
    ax.set_ylabel("# questions")
    for i, v in enumerate(vals):
        ax.text(i, v + 1, str(v), ha="center")
    fig.tight_layout()
    fig.savefig(FIG_DIR / "union_first_correct_source.png", dpi=150, bbox_inches="tight")
    plt.close(fig)

    print(f"\n图片已保存至: {FIG_DIR}")


if __name__ == "__main__":
    main()
