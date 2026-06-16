"""Tests for patch bundle: reversed schema, dedup, exec time tiebreak."""

from __future__ import annotations

import os
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from workflows.mcts_v4.utils.schema_diversity import (
    extract_tables_from_sql,
    enforce_fk_closure,
    prepare_schema_reversed_from_sqls,
    prepare_schema_combined_narrow_reversed,
    fk_pk_closure_enabled,
    combined_schema_linking_enabled,
)
from workflows.mcts_v4.utils.cte_diverse import (
    dedupe_candidates_before_revise,
    dedup_before_revise_enabled,
    reversed_bootstrap_direct_sql_enabled,
    bootstrap_once_per_question_enabled,
    skeleton_replace_plain_enabled,
    render_diverse_prompt,
)
from workflows.mcts_v4.utils.execution_tiebreak import (
    tiebreak_pick_variants,
    exec_time_tiebreak_enabled,
    clear_execution_time_cache,
    variants_have_row_collision,
    pick_representative_variant,
)
from workflows.mcts_v4.utils.mcts_helpers import MCTSUtils


SCHEMA = """db_name: test
CREATE TABLE frpm (
  CDSCode TEXT PRIMARY KEY,
  "School Name" TEXT
);
CREATE TABLE satscores (
  cds TEXT PRIMARY KEY,
  AvgScrWrite REAL
);
foreign_key: frpm.CDSCode = satscores.cds
"""


class TestReversedSchema(unittest.TestCase):
    def test_extract_tables_from_sql(self):
        sql = "SELECT f.CDSCode FROM frpm f JOIN satscores s ON f.CDSCode = s.cds"
        tables = extract_tables_from_sql(sql)
        self.assertIn("frpm", tables)
        self.assertIn("satscores", tables)

    def test_prepare_schema_reversed_from_sqls(self):
        sql = "SELECT * FROM frpm JOIN satscores ON frpm.CDSCode = satscores.cds"
        reduced, audit = prepare_schema_reversed_from_sqls(
            prior_sqls=[sql],
            schema_info=SCHEMA,
        )
        self.assertTrue(audit["linking_ok"])
        self.assertIn("frpm", reduced.lower())
        self.assertIn("satscores", reduced.lower())
        self.assertEqual(audit["strategy"], "reversed_prior_rollout")

    def test_fk_closure_adds_neighbor(self):
        closed, added = enforce_fk_closure(["frpm"], SCHEMA)
        self.assertIn("frpm", closed)
        self.assertIn("satscores", closed)
        self.assertIn("satscores", added)

    def test_reversed_with_fk_pk_flag(self):
        os.environ["MCTS_FK_PK_CLOSURE"] = "1"
        self.assertTrue(fk_pk_closure_enabled())
        sql = "SELECT * FROM frpm JOIN satscores ON frpm.CDSCode = satscores.cds"
        reduced, audit = prepare_schema_reversed_from_sqls(
            prior_sqls=[sql],
            schema_info=SCHEMA,
        )
        self.assertFalse(audit.get("fk_pk_closure"))
        self.assertIn("satscores", reduced.lower())
        os.environ.pop("MCTS_FK_PK_CLOSURE", None)

    def test_bootstrap_direct_sql_flag(self):
        os.environ["MCTS_REVERSED_BOOTSTRAP_DIRECT_SQL"] = "1"
        self.assertTrue(reversed_bootstrap_direct_sql_enabled())
        os.environ.pop("MCTS_REVERSED_BOOTSTRAP_DIRECT_SQL", None)

    def test_bootstrap_once_per_question_flag(self):
        os.environ["MCTS_BOOTSTRAP_ONCE_PER_QUESTION"] = "0"
        self.assertFalse(bootstrap_once_per_question_enabled())
        os.environ["MCTS_BOOTSTRAP_ONCE_PER_QUESTION"] = "1"
        self.assertTrue(bootstrap_once_per_question_enabled())
        os.environ.pop("MCTS_BOOTSTRAP_ONCE_PER_QUESTION", None)

    def test_skeleton_replace_plain_flag(self):
        os.environ["MCTS_CTE_SKELETON_REPLACE_PLAIN"] = "1"
        self.assertTrue(skeleton_replace_plain_enabled())
        prompt = render_diverse_prompt(
            n=2,
            original_question="oq",
            sub_question="sq",
            sub_question_index=0,
            sub_questions_total=1,
            schema_info="schema",
            additional_context="",
            preceding_cte_info="",
            used_cte_names=[],
            prompt_mode="skeleton",
        )
        self.assertIn("Plan", prompt)
        self.assertIn("Skeleton", prompt)
        os.environ.pop("MCTS_CTE_SKELETON_REPLACE_PLAIN", None)

    def test_combined_schema_linking_flag(self):
        os.environ["MCTS_COMBINED_SCHEMA_LINKING"] = "1"
        self.assertTrue(combined_schema_linking_enabled())
        os.environ.pop("MCTS_COMBINED_SCHEMA_LINKING", None)

    def test_prepare_schema_combined_narrow_reversed(self):
        from unittest.mock import patch

        sql = "SELECT * FROM satscores"
        with patch("workflows.mcts_v4.utils.schema_diversity.call_schema_linking") as mock_link:
            mock_link.return_value = ({"selected_tables": ["frpm"]}, "{}")
            reduced, audit = prepare_schema_combined_narrow_reversed(
                question="q",
                sub_question="sq",
                schema_info=SCHEMA,
                preceding_cte_info="",
                llm_config={"config_list": [{}]},
                original_schema_info=SCHEMA,
                prior_rollout_sqls=[sql],
            )
        self.assertTrue(audit["linking_ok"])
        self.assertEqual(audit["strategy"], "combined_narrow_reversed")
        self.assertFalse(audit.get("narrow_linking_reused"))
        self.assertEqual(audit["llm_calls_per_cte"], 2)
        self.assertIn("frpm", reduced.lower())
        self.assertIn("satscores", reduced.lower())
        mock_link.assert_called_once()

    def test_prepare_schema_combined_reuses_narrow_cache(self):
        from unittest.mock import patch

        sql = "SELECT * FROM satscores"
        cache = {
            "linking_obj": {"selected_tables": ["frpm"]},
            "linking_raw": "{}",
            "narrow_tables": ["frpm"],
            "reused": False,
        }
        with patch("workflows.mcts_v4.utils.schema_diversity.call_schema_linking") as mock_link:
            reduced, audit = prepare_schema_combined_narrow_reversed(
                question="q",
                sub_question="sq",
                schema_info=SCHEMA,
                preceding_cte_info="",
                llm_config={"config_list": [{}]},
                original_schema_info=SCHEMA,
                prior_rollout_sqls=[sql],
                cached_narrow_linking=cache,
            )
        self.assertTrue(audit["linking_ok"])
        self.assertTrue(audit["narrow_linking_reused"])
        self.assertEqual(audit["llm_calls_per_cte"], 1)
        self.assertIn("frpm", reduced.lower())
        self.assertIn("satscores", reduced.lower())
        mock_link.assert_not_called()


class TestDedupBeforeRevise(unittest.TestCase):
    def test_dedupe_normalized(self):
        os.environ["MCTS_DEDUP_BEFORE_REVISE"] = "1"
        self.assertTrue(dedup_before_revise_enabled())
        ctes = ["SELECT 1", "SELECT  1", "SELECT 2"]
        meta = {c: {"id": i} for i, c in enumerate(ctes)}
        kept, dropped = dedupe_candidates_before_revise(ctes, meta)
        self.assertEqual(len(kept), 2)
        self.assertEqual(dropped, 1)
        os.environ.pop("MCTS_DEDUP_BEFORE_REVISE", None)


class TestExecTimeTiebreak(unittest.TestCase):
    def test_tiebreak_by_time(self):
        os.environ["MCTS_EXEC_TIME_TIEBREAK"] = "1"
        clear_execution_time_cache()
        self.assertTrue(exec_time_tiebreak_enabled())
        db = MagicMock()
        times = {"select 1": 0.01, "select 2": 0.05}

        def exec_query(sql):
            import time

            time.sleep(times.get(sql.lower(), 0.001))
            return MagicMock(), None

        db.execute_query = exec_query
        variants = [("SELECT 1", 0.0, 1), ("SELECT 2", 0.0, 1)]
        picked = tiebreak_pick_variants(variants, db_connector=db)
        self.assertEqual(picked.upper(), "SELECT 1")
        os.environ.pop("MCTS_EXEC_TIME_TIEBREAK", None)
        clear_execution_time_cache()


class TestClusterSignatureAndTiebreak(unittest.TestCase):
    def test_bucketize_uses_v2_when_env_set(self):
        shared = [{"x": i, "y": i * 10} for i in range(5)]
        extra = [{"x": 99, "y": 990}]
        r_short = {"valid": True, "query_result": shared}
        r_long = {"valid": True, "query_result": shared + extra}
        os.environ["MCTS_USE_SIGNATURE_V2"] = "1"
        try:
            import workflows.mcts_v4.utils.mcts_helpers as mh

            mh.USE_SIGNATURE_V2_FOR_SEARCH = True
            buckets, _ = MCTSUtils.bucketize_valid_nonempty([r_short, r_long])
            self.assertEqual(len(buckets), 2)
            sig_short = MCTSUtils.cluster_signature(r_short)
            sig_long = MCTSUtils.cluster_signature(r_long)
            self.assertNotEqual(sig_short, sig_long)
            self.assertEqual(buckets[sig_short], 1)
            self.assertEqual(buckets[sig_long], 1)
        finally:
            os.environ.pop("MCTS_USE_SIGNATURE_V2", None)
            import workflows.mcts_v4.utils.mcts_helpers as mh

            mh.USE_SIGNATURE_V2_FOR_SEARCH = mh.os.environ.get("MCTS_USE_SIGNATURE_V2", "0") == "1"

    def test_tiebreak_collision_prefers_reward_over_min_rows(self):
        variants = [
            ("SELECT long wrong query", 0.5, 49),
            ("SELECT correct", 1.0, 57),
            ("SELECT medium wrong", 1.0, 99),
        ]
        self.assertTrue(variants_have_row_collision(variants))
        sql, reward, rows = pick_representative_variant(variants)
        self.assertEqual(reward, 1.0)
        self.assertIn(rows, (57, 99))
        picked = tiebreak_pick_variants(variants)
        self.assertIn(picked, ("SELECT correct", "SELECT medium wrong"))

    def test_tiebreak_uniform_rows_still_picks_min_rows(self):
        variants = [
            ("SELECT longer text here", 0.5, 3),
            ("SELECT short", 0.5, 3),
        ]
        self.assertFalse(variants_have_row_collision(variants))
        self.assertEqual(tiebreak_pick_variants(variants), "SELECT short")

    def test_r4_all_buckets_votes_every_sig_in_rollout(self):
        from workflows.mcts_v4.utils.r4_vote import ENV_CLUSTER_VOTE_MODE, collect_r4_cluster_votes

        rss = [{"result_buckets": {"sig_a": 3, "sig_b": 1}}]
        prev = os.environ.get(ENV_CLUSTER_VOTE_MODE)
        try:
            os.environ[ENV_CLUSTER_VOTE_MODE] = "mc"
            self.assertEqual(dict(collect_r4_cluster_votes(rss)), {"sig_a": 1})
            os.environ[ENV_CLUSTER_VOTE_MODE] = "all_buckets"
            self.assertEqual(dict(collect_r4_cluster_votes(rss)), {"sig_a": 1, "sig_b": 1})
        finally:
            if prev is None:
                os.environ.pop(ENV_CLUSTER_VOTE_MODE, None)
            else:
                os.environ[ENV_CLUSTER_VOTE_MODE] = prev

    def test_vote_tie_gate_includes_all_tied_sigs(self):
        from workflows.mcts_v4.utils.gated_selection import _analyze_r4_gate

        rss = [
            {
                "reward": 1.0,
                "leaf_visit_count": 1,
                "result_buckets": {"sig_a": 1, "sig_b": 1, "sig_c": 1, "sig_d": 1},
                "all_sql_variants": [
                    {"sql": "SELECT 1", "valid": True, "result_signature": "sig_a", "result_row_count": 1},
                    {"sql": "SELECT 2", "valid": True, "result_signature": "sig_b", "result_row_count": 1},
                    {"sql": "SELECT 3", "valid": True, "result_signature": "sig_c", "result_row_count": 1},
                    {"sql": "SELECT 4", "valid": True, "result_signature": "sig_d", "result_row_count": 1},
                ],
            }
        ]
        r4 = _analyze_r4_gate(rss, 0.7)
        self.assertTrue(r4.ambiguous)
        self.assertEqual(r4.gate_reason, "vote_tie")
        self.assertEqual(set(r4.gate_sigs), {"sig_a", "sig_b", "sig_c", "sig_d"})

    def test_mul_purity_default_score_mode(self):
        script = Path(__file__).resolve().parents[1] / "config" / "bprime_env.sh"
        text = script.read_text(encoding="utf-8")
        self.assertIn('MCTS_R4_SCORE_MODE="${MCTS_R4_SCORE_MODE:-mul_purity}"', text)
        self.assertIn('MCTS_R4_TOPK_BOOTSTRAP="${MCTS_R4_TOPK_BOOTSTRAP:-ambig_purity}"', text)

    def test_topk_bootstrap_skips_clear_gate(self):
        from workflows.mcts_v4.utils.sql_selector import SQLSelector

        rss = [
            {
                "reward": 1.0,
                "leaf_visit_count": 3,
                "result_buckets": {"sig_a": 10},
                "all_sql_variants": [
                    {
                        "sql": "SELECT 1",
                        "valid": True,
                        "result_signature": "sig_a",
                        "result_signature_v2": "v2_a",
                        "result_row_count": 1,
                    },
                ],
            },
        ]
        prev = {
            k: os.environ.get(k)
            for k in (
                "MCTS_R4_SCORE_MODE",
                "MCTS_R4_TOPK_BOOTSTRAP",
                "MCTS_R4_WITH_BIAS",
            )
        }
        try:
            os.environ["MCTS_R4_SCORE_MODE"] = "votes"
            os.environ["MCTS_R4_TOPK_BOOTSTRAP"] = "0"
            os.environ["MCTS_R4_WITH_BIAS"] = "0"
            sql = SQLSelector._select_r4_majority_then_reward(rss, db_connector=object())
            self.assertEqual(sql, "SELECT 1")
        finally:
            for k, v in prev.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v


class TestTaskSpill(unittest.TestCase):
    def setUp(self):
        self._old_env = os.environ.get("MCTS_TASK_SPILL_DIR")
        self._tmpdir = Path(os.environ.get("TMPDIR", "/tmp")) / "task_spill_test"
        self._tmpdir.mkdir(parents=True, exist_ok=True)
        os.environ["MCTS_TASK_SPILL_DIR"] = str(self._tmpdir)
        os.environ["MCTS_TASK_SPILL"] = "1"

    def tearDown(self):
        if self._old_env is None:
            os.environ.pop("MCTS_TASK_SPILL_DIR", None)
        else:
            os.environ["MCTS_TASK_SPILL_DIR"] = self._old_env
        for p in self._tmpdir.glob("*.json"):
            p.unlink(missing_ok=True)

    def test_write_read_and_select(self):
        from workflows.mcts_v4.utils.task_spill import (
            build_spill_payload,
            read_task_spill,
            select_sql_from_spill,
            spill_has_selectable_candidates,
            write_task_spill,
        )

        rss = [
            {
                "rollout_id": 1,
                "selected_sql": "SELECT 1",
                "reward": 1.0,
                "result_buckets": {"sig_a": 2},
                "all_sql_variants": [
                    {
                        "sql": "SELECT 1",
                        "reward": 1.0,
                        "result_signature": "sig_a",
                        "valid": True,
                        "result_row_count": 1,
                    }
                ],
            },
            {
                "rollout_id": 2,
                "selected_sql": "SELECT 2",
                "reward": 0.5,
                "result_buckets": {"sig_b": 1},
                "all_sql_variants": [
                    {
                        "sql": "SELECT 2",
                        "reward": 0.5,
                        "result_signature": "sig_b",
                        "valid": True,
                        "result_row_count": 1,
                    }
                ],
            },
        ]
        payload = build_spill_payload(
            qid="99",
            idx=99,
            question="q",
            schema_info="schema",
            rollout_stats_list=rss,
        )
        self.assertTrue(spill_has_selectable_candidates(payload["rollout_stats"]))
        write_task_spill(payload)
        loaded = read_task_spill("99")
        self.assertIsNotNone(loaded)
        self.assertEqual(len(loaded["rollout_stats"]), 2)
        os.environ["MCTS_SELECTOR_STRATEGY"] = "R4"
        sql = select_sql_from_spill(loaded, db_connector=None, llm_config=None)
        self.assertIn(sql.upper(), ("SELECT 1", "SELECT 2"))
        os.environ.pop("MCTS_SELECTOR_STRATEGY", None)

    def test_bootstrap_merged_when_no_rollouts(self):
        from workflows.mcts_v4.utils.task_spill import (
            build_spill_payload,
            spill_has_selectable_candidates,
        )

        payload = build_spill_payload(
            qid="100",
            idx=100,
            question="q",
            schema_info="schema",
            rollout_stats_list=[],
            bootstrap_sql="SELECT bootstrap",
        )
        self.assertTrue(spill_has_selectable_candidates(payload["rollout_stats"]))
        self.assertEqual(payload["rollout_stats"][0]["source"], "bootstrap_direct_sql")


if __name__ == "__main__":
    unittest.main()
