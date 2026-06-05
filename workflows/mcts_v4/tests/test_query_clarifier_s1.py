"""S1: default-off import and noop behavior."""

import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from workflows.mcts_v4.query_clarifier import (
    ENV_ENABLE,
    clarify_enabled,
    maybe_apply_clarify,
    noop_clarify,
)
from workflows.mcts_v4.query_clarifier.config import clarify_mode
from workflows.mcts_v4.query_clarifier.schemas import NodeStats


def test_default_disabled():
    os.environ.pop(ENV_ENABLE, None)
    assert clarify_enabled() is False
    assert clarify_mode() == "log_only"


def test_noop_unchanged_rollouts():
    rss = [{"reward": 1.0, "all_sql_variants": [{"sql": "SELECT 1", "valid": True}]}]
    out, rec = noop_clarify(rss, qid=1)
    assert out is rss
    assert rec is None


def test_maybe_apply_clarify_when_disabled():
    os.environ.pop(ENV_ENABLE, None)
    rss = [{"reward": 1.0, "result_buckets": {"a": 1, "b": 1}, "all_sql_variants": []}]
    out, rec = maybe_apply_clarify(rss, qid=1, nl_question="q", schema_ddl="ddl")
    assert out is rss
    assert rec is None


def test_schemas_node_stats():
    ns = NodeStats(high_reward_rollouts=6, n_nonempty_result_buckets=2)
    assert ns.top1_visit == 0
