"""Unit tests for diverse-CTE mode C (MCTS pipeline)."""

import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_ROOT = Path(__file__).resolve().parent.parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from workflows.mcts_v4.utils import cte_diverse as cd


def test_template_renders_with_n_and_schema():
    prompt = cd.render_diverse_prompt(
        n=5,
        original_question="How many schools?",
        sub_question="Count schools in CA",
        sub_question_index=0,
        sub_questions_total=2,
        schema_info="db_name:test\n# table schools(id INT)",
        additional_context="evidence: CA means California",
        preceding_cte_info="No preceding CTE",
        used_cte_names=["ca_schools"],
    )
    assert "Generate exactly 5" in prompt
    assert "db_name:test" in prompt
    assert "ca_schools" in prompt


def test_parse_malformed_json_returns_empty():
    assert cd.parse_diverse_json("not json at all") == []
    assert cd.parse_diverse_json('{"foo": 1}') == []


def test_parse_valid_json():
    raw = json.dumps(
        {
            "decompositions": [
                {"id": 1, "rationale": "a", "cte_sql": "WITH c1 AS (SELECT 1)"},
                {"id": 2, "rationale": "b", "cte_sql": "WITH c2 AS (SELECT 2)"},
            ]
        }
    )
    items = cd.parse_diverse_json(raw)
    assert len(items) == 2


def test_dedupe_removes_structurally_identical_ctes():
    cte_a = "WITH x AS (SELECT id FROM t WHERE state = 'CA')\nSELECT * FROM x;"
    cte_b = "WITH y AS (SELECT id FROM t WHERE state = 'CA')\nSELECT * FROM y;"
    cte_c = "WITH z AS (SELECT count(*) FROM t)\nSELECT * FROM z;"
    kept, dropped = cd.dedupe_by_signature([cte_a, cte_b, cte_c], set())
    assert len(kept) == 2
    assert dropped == 1


def test_generate_mode_c_dedupes_across_temps():
    node = MagicMock()
    node.question = "q"
    node.schema_info = "schema"
    node.additional_context = ""
    node.sub_questions_total = 1
    node.sub_question_index = 0
    node.sub_question = "q"
    node._original_question = "q"

    payloads = [
        json.dumps(
            {
                "decompositions": [
                    {"id": 1, "rationale": "a", "cte_sql": "WITH a AS (SELECT 1 FROM t)\nSELECT * FROM a;"},
                    {"id": 2, "rationale": "b", "cte_sql": "WITH b AS (SELECT count(*) FROM t)\nSELECT * FROM b;"},
                ]
            }
        ),
        json.dumps(
            {
                "decompositions": [
                    {"id": 3, "rationale": "dup", "cte_sql": "WITH c AS (SELECT 1 FROM t)\nSELECT * FROM c;"},
                    {"id": 4, "rationale": "c", "cte_sql": "WITH d AS (SELECT 2 FROM t)\nSELECT * FROM d;"},
                ]
            }
        ),
    ]
    call_idx = {"i": 0}

    def fake_call(**kwargs):
        out = payloads[call_idx["i"]]
        call_idx["i"] += 1
        return out

    def fake_extract(text):
        if "count(*)" in text:
            return "WITH b AS (SELECT count(*) FROM t)\nSELECT * FROM b;"
        if "SELECT 2" in text:
            return "WITH d AS (SELECT 2 FROM t)\nSELECT * FROM d;"
        return "WITH a AS (SELECT 1 FROM t)\nSELECT * FROM a;"

    with patch.object(cd, "call_diverse_prompt", side_effect=fake_call):
        ctes, trace = cd.generate_diverse_mode_c(
            node=node,
            llm_config={"config_list": [{"model": "m", "base_url": "http://x", "api_key": "k"}]},
            extract_fn=fake_extract,
            n_per_call=2,
            preceding_cte_info="none",
            used_cte_names=[],
            temperatures=[0.3, 0.6],
        )
    assert len(ctes) >= 2
    assert trace["mode"] == "C"
    assert trace["n_candidates"] == len(ctes)
    assert len(trace["diverse_kept"]) == len(ctes)


def test_flag_off_diverse_disabled():
    env = os.environ.copy()
    env.pop(cd.ENV_DIVERSE_PROMPT, None)
    with patch.dict(os.environ, env, clear=True):
        assert cd.diverse_prompt_enabled() is False


def test_diverse_temps_default_and_env():
    with patch.dict(os.environ, {}, clear=True):
        assert cd.diverse_temps() == [0.3, 0.6, 0.9]
    with patch.dict(os.environ, {cd.ENV_DIVERSE_TEMPS: "0.3,0.6,0.9"}, clear=True):
        assert cd.diverse_temps() == [0.3, 0.6, 0.9]


def test_schema_diversity_default_follows_diverse_prompt():
    from workflows.mcts_v4.utils import schema_diversity as sd

    with patch.dict(os.environ, {cd.ENV_DIVERSE_PROMPT: "1"}, clear=True):
        assert sd.schema_diversity_enabled() is True
    with patch.dict(os.environ, {cd.ENV_DIVERSE_PROMPT: "1", sd.ENV_SCHEMA_DIVERSITY: "0"}, clear=True):
        assert sd.schema_diversity_enabled() is False
    with patch.dict(os.environ, {}, clear=True):
        assert sd.schema_diversity_enabled() is False


def test_skip_m_verify_flag():
    with patch.dict(os.environ, {cd.ENV_SKIP_M_VERIFY: "1"}, clear=True):
        assert cd.skip_m_verify_enabled() is True
    with patch.dict(os.environ, {}, clear=True):
        assert cd.skip_m_verify_enabled() is False


def test_sql_gen_temps_align_with_cte_diverse():
    from workflows.mcts_v4.agents.complete_sql_generator import sql_gen_temperatures

    with patch.dict(
        os.environ,
        {
            cd.ENV_DIVERSE_PROMPT: "1",
            cd.ENV_DIVERSE_TEMPS: "0.3,0.6,0.9",
        },
        clear=True,
    ):
        assert sql_gen_temperatures() == [0.3, 0.6, 0.9]

    with patch.dict(
        os.environ,
        {
            cd.ENV_DIVERSE_PROMPT: "1",
            cd.ENV_DIVERSE_TEMPS: "0.3,0.6,0.9",
            "MCTS_SQL_GEN_TEMPS": "0.3,0.6",
        },
        clear=True,
    ):
        assert sql_gen_temperatures() == [0.3, 0.6]
