"""Fidelity checks for LLM-rendered clarification questions."""

from __future__ import annotations

from .data_structures import DecisionAxis


def validate_fidelity(
    rendered: dict,
    axis: DecisionAxis,
) -> tuple[bool, str]:
    """
    Verify LLM JSON matches the decision axis structure.

    Returns ``(passed, failure_reason)``.
    """
    if not isinstance(rendered, dict):
        return False, "response is not a JSON object"

    focus = rendered.get("semantic_focus")
    if not isinstance(focus, str) or not focus.strip():
        return False, "semantic_focus empty"
    focus = focus.strip()
    if len(focus) < 1 or len(focus) > 50:
        return False, f"semantic_focus length {len(focus)} out of range [1, 50]"

    options = rendered.get("options")
    if not isinstance(options, list):
        return False, "options is not a list"

    expected_k = axis.num_branches
    if len(options) != expected_k:
        return False, (
            f"option count {len(options)} != branch count {expected_k}"
        )

    partition_keys = set(axis.partition.keys())
    seen_keys: set[str] = set()

    for i, opt in enumerate(options):
        if not isinstance(opt, dict):
            return False, f"option[{i}] is not an object"
        branch_key = opt.get("branch_key")
        nl_text = opt.get("nl_text")
        if not isinstance(branch_key, str):
            return False, f"option[{i}].branch_key missing or not string"
        if branch_key not in partition_keys:
            return False, f"option[{i}].branch_key {branch_key!r} not in partition"
        if branch_key in seen_keys:
            return False, f"duplicate branch_key {branch_key!r}"
        seen_keys.add(branch_key)
        if not isinstance(nl_text, str) or not nl_text.strip():
            return False, f"option[{i}].nl_text empty"
        nl_text = nl_text.strip()
        if len(nl_text) < 1 or len(nl_text) > 200:
            return False, f"option[{i}].nl_text length {len(nl_text)} out of range"

    if seen_keys != partition_keys:
        missing = partition_keys - seen_keys
        return False, f"missing branch keys: {sorted(missing)}"

    return True, ""
