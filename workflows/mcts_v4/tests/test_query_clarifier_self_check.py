"""Case C / v0.2: AST extraction self-check against rep_sql."""

import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from workflows.mcts_v4.query_clarifier.constraint import candidate_passes_self_check, compile_constraint, rep_sql_for_rank
from workflows.mcts_v4.query_clarifier.extractor import extract_constraint_hint
from workflows.mcts_v4.query_clarifier.schemas import (
    ClarificationAnswer,
    ClarificationCandidate,
    ClarificationQuestion,
    ClusterSummary,
)
from workflows.mcts_v4.query_clarifier.triggers import build_cluster_summaries

CALIB = _ROOT / "workflows/mcts_v4/test/out/clarify_a0_a2_coder/v4_calib_498q_coder_rollouts8.json"
TRACE = _ROOT / "workflows/mcts_v4/test/out/clarify_a0_a2_coder/analysis/clarify_v0_log_only_100q.trace.jsonl"
T8_QIDS = ["31", "50", "347", "915", "1037", "1238", "1275"]


def _extract_candidates_from_trace(qid: str, calib: dict, trace_row: dict):
    rec = calib[qid]
    clusters = build_cluster_summaries(rec["rollout_stats"])
    cl = trace_row["clarify"]
    axis = cl["axis"]
    cands = []
    for i, c in enumerate(cl.get("candidates") or []):
        rank = int(
            c.get("maps_to_cluster_rank")
            or {"A": 1, "B": 2, "C": 3, "D": 4}.get(c["cid"], i + 1)
        )
        rep = rep_sql_for_rank(clusters, rank)
        hint = extract_constraint_hint(axis, rep)
        if hint is None:
            continue
        cands.append(
            ClarificationCandidate(c["cid"], c["summary"], hint, maps_to_cluster_rank=rank)
        )
    return axis, clusters, cands


def test_t8_qids_extracted_hints_pass_self_check():
    calib = json.loads(CALIB.read_text(encoding="utf-8"))
    traces = {}
    for line in TRACE.read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            traces[str(row["qid"])] = row
    passed = 0
    for qid in T8_QIDS:
        axis, clusters, cands = _extract_candidates_from_trace(qid, calib, traces[qid])
        assert cands, f"qid={qid} no extractable candidates"
        for cand in cands:
            assert candidate_passes_self_check(cand, axis, clusters), f"qid={qid} cid={cand.cid}"
        passed += len(cands)
    assert passed >= len(T8_QIDS)


def test_happy_path_self_check_passes():
    sql = "SELECT COUNT(*) AS n FROM schools WHERE County = 'Orange'"
    clusters = [
        ClusterSummary(1, 5, 0.9, sql, "sig_a"),
        ClusterSummary(2, 3, 0.8, "SELECT COUNT(DISTINCT id) FROM schools", "sig_b"),
    ]
    hint_a = extract_constraint_hint("Measure", sql)
    hint_b = extract_constraint_hint("Measure", clusters[1].representative_sql)
    cq = ClarificationQuestion(
        1,
        "Measure",
        "q",
        [
            ClarificationCandidate("A", "count all", hint_a or {}, maps_to_cluster_rank=1),
            ClarificationCandidate("B", "count distinct", hint_b or {}, maps_to_cluster_rank=2),
        ],
    )
    ans = ClarificationAnswer(1, "A", 0.9, "evidence", abstain=False)
    cc = compile_constraint(cq, ans, top_clusters=clusters)
    assert cc.self_check_failed is False
    assert cc.level == "hard"
