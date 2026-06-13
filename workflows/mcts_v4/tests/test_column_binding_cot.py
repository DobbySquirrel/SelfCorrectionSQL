"""Tests for SQL-pre column binding CoT (unified single-call)."""

import os
from unittest.mock import patch

from workflows.mcts_v4.utils import column_binding_cot as cb


def test_format_binding_block():
    block = cb.format_binding_block(
        "Column: patients.SEX\n  Condition: = 'F'\n  Function: none"
    )
    assert "column binding" in block
    assert "patients.SEX" in block


def test_parse_column_binding():
    text = """Column: diagnoses.Description
  Condition: year = '1997'
  Function: STRFTIME('%Y', Description)
  Reasoning: use Description not First Date"""
    parsed = cb.parse_column_binding(text)
    assert parsed["n_columns"] == 1
    assert parsed["columns"][0]["column"] == "diagnoses.Description"
    assert "1997" in parsed["columns"][0]["condition"]


def test_enabled_env():
    with patch.dict(os.environ, {cb.ENV_COLUMN_BINDING_COT: "1"}):
        assert cb.column_binding_mode() == cb.MODE_UNIFIED
        assert cb.column_binding_cot_enabled()
        assert cb.column_binding_unified_enabled()
        assert not cb.column_binding_per_subq_enabled()
    with patch.dict(os.environ, {cb.ENV_COLUMN_BINDING_COT: "per_subq@0.3"}):
        assert cb.column_binding_mode() == cb.MODE_PER_SUBQ
        assert cb.column_binding_temp_gate() == 0.3
        assert cb.column_binding_applies_at_temp(0.3)
        assert not cb.column_binding_applies_at_temp(0.6)
        assert not cb.column_binding_dual_low_temp()
        assert cb.column_binding_replace_at_temp(0.3)
    with patch.dict(os.environ, {cb.ENV_COLUMN_BINDING_COT: "per_subq@0.3+dual", cb.ENV_COLUMN_BINDING_SCOPE: "global"}):
        assert cb.column_binding_dual_low_temp()
        assert cb.column_binding_scope_global()
        assert not cb.column_binding_scope_per_subq_decompose()
        assert cb.column_binding_dual_at_temp(0.3)
        assert not cb.column_binding_dual_at_temp(0.6)
        assert not cb.column_binding_replace_at_temp(0.3)
        assert cb.column_binding_applies_at_temp(0.3)
    with patch.dict(os.environ, {cb.ENV_COLUMN_BINDING_COT: "per_subq@0.3+dual", cb.ENV_COLUMN_BINDING_SCOPE: "per_subq"}):
        assert cb.column_binding_scope_per_subq_decompose()
        assert not cb.column_binding_scope_global()
        assert cb.binding_cache_key(sub_question_index=2, preceding_cte_info="cte_a") == "sq:2"
    with patch.dict(os.environ, {cb.ENV_COLUMN_BINDING_COT: "0"}, clear=False):
        assert cb.column_binding_mode() is None
        assert not cb.column_binding_cot_enabled()


def test_augment_skipped_when_disabled():
    with patch.dict(os.environ, {cb.ENV_COLUMN_BINDING_COT: "0"}):
        out, audit = cb.augment_additional_context_with_column_binding(
            question="q",
            schema_info="schema",
            additional_context="evidence",
            llm_config={"config_list": [{}]},
        )
    assert out == "evidence"
    assert audit is None


def test_run_column_binding_cot_mocked():
    unified = """Column: Patient.Description
  Condition: year = '1997'
  Function: STRFTIME('%Y', Description)
  Reasoning: evidence says year(Description)"""
    with patch.object(cb, "_call_unified_binding", return_value=unified):
        block, audit = cb.run_column_binding_cot(
            question="How many?",
            schema_info="CREATE TABLE t(id INT)",
            additional_context="evidence: x",
            llm_config={"config_list": [{"base_url": "http://x/v1", "model": "m"}]},
        )
    assert "evidence: x" in block
    assert "Patient.Description" in block
    assert audit["llm_calls"] == 1
    assert audit["mode"] == "unified"
    assert audit["parsed"]["n_columns"] == 1


def test_per_subq_decompose_scope_reuses_cache_across_preceding_ctes():
    calls = {"n": 0}

    def fake_binding(**kwargs):
        calls["n"] += 1
        return "Column: t.c\n  Condition: x\n  Function: none\n  Reasoning: y"

    cache = {}
    llm = {"config_list": [{"base_url": "http://x/v1", "model": "m"}]}
    env = {
        cb.ENV_COLUMN_BINDING_COT: "per_subq@0.3+dual",
        cb.ENV_COLUMN_BINDING_SCOPE: "per_subq",
    }
    with patch.dict(os.environ, env, clear=False):
        with patch.object(cb, "_call_per_subq_binding", side_effect=fake_binding) as mock:
            cb.run_column_binding_per_subq(
                original_question="Q",
                sub_question="step1",
                sub_question_index=1,
                preceding_cte_info="path_a",
                schema_info="schema",
                additional_context="evidence",
                llm_config=llm,
                cache=cache,
            )
            cb.run_column_binding_per_subq(
                original_question="Q",
                sub_question="step1",
                sub_question_index=1,
                preceding_cte_info="path_b",
                schema_info="schema",
                additional_context="evidence",
                llm_config=llm,
                cache=cache,
            )
    assert calls["n"] == 1
    assert mock.call_count == 1
    assert mock.call_args.kwargs["preceding_cte_info"] == "No preceding CTE"
