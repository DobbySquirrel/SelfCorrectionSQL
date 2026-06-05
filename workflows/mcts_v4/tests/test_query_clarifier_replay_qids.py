"""Test --qids_file loading for replay script."""

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from workflows.mcts_v4.query_clarifier.replay_log_only_100q import load_qids_from_file, resolve_qids

SMOKE = _ROOT / "workflows/mcts_v4/test/out/clarify_a0_a2_coder/analysis/s8_20q_smoke_qids.txt"


def test_load_smoke_qids():
    qids = load_qids_from_file(SMOKE)
    assert len(qids) == 20
    assert qids[0] == "32"
    assert qids[-1] == "371"


def test_resolve_qids_missing_fatal(tmp_path):
    calib = {"32": {"rollout_stats": []}}
    qf = tmp_path / "q.txt"
    qf.write_text("32\n999\n", encoding="utf-8")
    try:
        resolve_qids(calib, qids_file=qf, n_q=10, seed=0)
        assert False, "expected SystemExit"
    except SystemExit as e:
        assert "999" in str(e)
