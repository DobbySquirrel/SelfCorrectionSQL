"""Tests for patch bundle: reversed schema, dedup, exec time tiebreak."""

from __future__ import annotations

import os
import unittest
from unittest.mock import MagicMock

from workflows.mcts_v4.utils.schema_diversity import (
    extract_tables_from_sql,
    enforce_fk_closure,
    prepare_schema_reversed_from_sqls,
    fk_pk_closure_enabled,
)
from workflows.mcts_v4.utils.cte_diverse import (
    dedupe_candidates_before_revise,
    dedup_before_revise_enabled,
    reversed_bootstrap_direct_sql_enabled,
    bootstrap_once_per_question_enabled,
)
from workflows.mcts_v4.utils.execution_tiebreak import (
    tiebreak_pick_variants,
    exec_time_tiebreak_enabled,
    clear_execution_time_cache,
)


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


if __name__ == "__main__":
    unittest.main()
