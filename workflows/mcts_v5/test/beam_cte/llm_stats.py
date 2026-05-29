"""Optional LLM call / token counters for beam-CTE drivers."""

from __future__ import annotations

from typing import Any, List, Optional

from workflows.mcts_v5.llm.types import LLMResponse


class CountingLLM:
    """Thin wrapper around a ChatLLM that tracks usage."""

    def __init__(self, inner: Any):
        self._inner = inner
        self.llm_calls_total = 0
        self.tokens_total = 0

    def complete(
        self,
        messages: list[dict],
        temperature: float = 1.0,
        max_tokens: int = 1024,
        stop: list[str] | None = None,
    ) -> LLMResponse:
        self.llm_calls_total += 1
        resp = self._inner.complete(
            messages,
            temperature=temperature,
            max_tokens=max_tokens,
            stop=stop,
        )
        text = resp.text or ""
        self.tokens_total += max(1, len(text) // 4)
        return resp

    def sample_parallel(self, *args, **kwargs) -> List[LLMResponse]:
        n = kwargs.get("n") or (args[1] if len(args) > 1 else 1)
        self.llm_calls_total += int(n)
        out = self._inner.sample_parallel(*args, **kwargs)
        for r in out:
            self.tokens_total += max(1, len((r.text or "")) // 4)
        return out

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    def reset_counters(self) -> None:
        self.llm_calls_total = 0
        self.tokens_total = 0
