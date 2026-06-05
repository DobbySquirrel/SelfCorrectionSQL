"""Shared LLM client helpers for Clarify / Answer agents."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Callable, Dict, Optional

_PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"


def load_prompt(name: str) -> str:
    return (_PROMPTS_DIR / name).read_text(encoding="utf-8")


def extract_json(text: str) -> Optional[Dict[str, Any]]:
    text = (text or "").strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group())
    except json.JSONDecodeError:
        return None


def default_openai_client():
    from openai import OpenAI
    from workflows.mcts_v4.utils.model_utils import pick_model

    base_url = os.environ.get("VLLM_API_URL", "http://127.0.0.1:8000/v1")
    api_key = os.environ.get("VLLM_API_KEY", "dummy-key")
    model = pick_model(base_url, api_key)
    client = OpenAI(base_url=base_url, api_key=api_key, timeout=120.0)
    return client, model


def call_llm_json(
    prompt: str,
    *,
    mock_fn: Optional[Callable[[], Dict[str, Any]]] = None,
    client=None,
    model: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    if mock_fn is not None:
        return mock_fn()
    if client is None or model is None:
        client, model = default_openai_client()
    resp = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.2,
    )
    text = resp.choices[0].message.content or ""
    return extract_json(text)
