"""Question pools and selectors for Phase C offline replay."""

from __future__ import annotations

import math
import random
from abc import ABC, abstractmethod
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .items import InteractionItem
from .user_simulator import OptionSpec


@dataclass
class World:
  hash: str
  sample_count: int
  representative_sql: str


@dataclass
class Question:
  options: list[OptionSpec]
  source: str
  metadata: dict[str, Any] = field(default_factory=dict)
  scope: tuple[str, ...] = ()
  qid: str = ""

  def __post_init__(self) -> None:
    if not self.qid:
      object.__setattr__(self, "qid", self.source)


def _option_keys(opt: OptionSpec) -> set[str]:
  if opt.item_keys:
    return set(opt.item_keys)
  return set(opt.world_hashes)


def _entropy_weights(weights: list[float]) -> float:
  total = sum(weights)
  if total <= 0:
    return 0.0
  h = 0.0
  for w in weights:
    if w > 0:
      p = w / total
      h -= p * math.log2(p)
  return h


def compute_eig(question: Question, items: list[InteractionItem]) -> float:
  """EIG over items weighted by weight (worlds or candidates)."""
  if not items:
    return 0.0
  total = sum(it.weight for it in items)
  if total <= 0:
    return 0.0
  p_h = {it.key: it.weight / total for it in items}
  h_w = -sum(p * math.log2(p) for p in p_h.values() if p > 0)

  cond = 0.0
  for opt in question.options:
    keys = _option_keys(opt)
    p_o = sum(p_h.get(k, 0.0) for k in keys)
    if p_o <= 0:
      continue
    inner = [p_h[k] / p_o for k in keys if k in p_h]
    cond += p_o * _entropy_weights(inner)
  return h_w - cond


def items_from_worlds(worlds: list[World]) -> list[InteractionItem]:
  return [
    InteractionItem(
      key=w.hash,
      weight=w.sample_count,
      exec_hash=w.hash,
      representative_sql=w.representative_sql,
    )
    for w in worlds
  ]


class ClusterPool:
  """Baseline: each question is a binary partition of W."""

  EXHAUSTIVE_MAX = 8
  RANDOM_SAMPLE_N = 200

  def build(self, items: list[InteractionItem]) -> list[Question]:
    n = len(items)
    if n < 2:
      return []

    questions: list[Question] = []
    keys = [it.key for it in items]

    if n <= self.EXHAUSTIVE_MAX:
      for mask in range(1, 1 << n):
        if mask == (1 << n) - 1:
          continue
        left = {keys[i] for i in range(n) if mask & (1 << i)}
        right = {keys[i] for i in range(n) if not (mask & (1 << i))}
        questions.append(self._binary_question(items, left, right, mask))
    else:
      questions = self._random_binary_questions(items, keys, n)

    return questions

  def _random_binary_questions(
      self,
      items: list[InteractionItem],
      keys: list[str],
      n: int,
  ) -> list[Question]:
    rng = random.Random(0)
    seen: set[frozenset[str]] = set()
    questions: list[Question] = []
    full = (1 << n) - 1
    attempts = 0
    max_attempts = self.RANDOM_SAMPLE_N * 50

    while len(questions) < self.RANDOM_SAMPLE_N and attempts < max_attempts:
      attempts += 1
      mask = rng.randrange(1, full)
      left = {keys[i] for i in range(n) if mask & (1 << i)}
      key = frozenset(left)
      if key in seen or not left or len(left) == n:
        continue
      seen.add(key)
      right = {keys[i] for i in range(n) if not (mask & (1 << i))}
      questions.append(self._binary_question(items, left, right, mask))

    if not questions:
      ordered = sorted(items, key=lambda it: (-it.weight, it.key))
      left = {ordered[0].key}
      right = {it.key for it in ordered[1:]}
      questions.append(self._binary_question(items, left, right, 0))
    return questions

  def _binary_question(
      self,
      items: list[InteractionItem],
      left: set[str],
      right: set[str],
      tag: int,
  ) -> Question:
    by_key = {it.key: it for it in items}

    def _rep(kset: set[str]) -> str:
      for k in kset:
        if k in by_key:
          return by_key[k].representative_sql
      return ""

    rep_a = _rep(left)
    rep_b = _rep(right)
    scope: tuple[str, ...] = ()
    if rep_a and rep_b:
      from experiment.pipeline.openworld.oracle_hint import scope_from_ast_diff
      scope = scope_from_ast_diff(rep_a, rep_b)

    return Question(
      options=[
        OptionSpec(
          label=f"Group A ({len(left)} worlds)",
          world_hashes=left,
          representative_sql=rep_a,
        ),
        OptionSpec(
          label=f"Group B ({len(right)} worlds)",
          world_hashes=right,
          representative_sql=rep_b,
        ),
      ],
      source=f"cluster:split:{tag}",
      scope=scope,
      qid=f"cluster:split:{tag}",
    )


class AtomicPool:
  """Main: one question per DSL (family, parameter) disagreement axis."""

  def __init__(
      self,
      db_path: str | Path | None = None,
      *,
      use_nl_rendering: bool = False,
      llm_client: Any | None = None,
      question_text: str = "",
  ):
    self.db_path = str(db_path) if db_path else None
    self.use_nl_rendering = use_nl_rendering
    self.llm_client = llm_client
    self.question_text = question_text or ""

  def build(self, items: list[InteractionItem]) -> list[Question]:
    if self.use_nl_rendering:
      from question_generation.pool_builder import build_pool_from_items

      return build_pool_from_items(
          items,
          self.question_text,
          self.llm_client,
          use_nl_rendering=True,
          db_path=self.db_path,
      )

    from experiment.pipeline.ast import (
      dsl_available,
      extract_dsl_variables_for_candidates,
    )

    if not dsl_available() or len(items) < 2:
      return []

    pairs = [(it.key, it.representative_sql) for it in items]
    dvars, _errs = extract_dsl_variables_for_candidates(
      pairs, db_path=self.db_path,
    )

    key_set = {it.key for it in items}
    by_key = {it.key: it for it in items}
    questions: list[Question] = []

    for v in dvars:
      value_to_keys: dict[str, set[str]] = defaultdict(set)
      for cid, val in v.candidate_to_value.items():
        if cid in key_set:
          value_to_keys[val].add(cid)

      if len(value_to_keys) < 2:
        continue

      options: list[OptionSpec] = []
      for value in sorted(value_to_keys.keys()):
        hashes = value_to_keys[value]
        rep = next(by_key[h].representative_sql for h in hashes if h in by_key)
        label = f"{v.family}:{v.parameter}={value}"
        options.append(OptionSpec(
          label=label[:80],
          world_hashes=hashes,
          representative_sql=rep,
        ))

      covered: set[str] = set()
      for o in options:
        covered |= o.world_hashes

      if len(options) >= 2 and covered == key_set:
        from experiment.pipeline.openworld.oracle_hint import scope_from_atomic
        qid = f"atomic:{v.family}:{v.parameter}"
        questions.append(Question(
          options=options,
          source=qid,
          metadata={"family": v.family, "parameter": v.parameter},
          scope=scope_from_atomic(v.family, v.parameter),
          qid=qid,
        ))

    return questions


class TokenDiffPool:
  """Qiu et al. baseline: token-level k-way questions on per-SQL candidates."""

  def build(self, items: list[InteractionItem]) -> list[Question]:
    from experiment.pipeline.ast.token_diff import build_token_question_drafts
    from experiment.pipeline.candidates import Candidate

    if len(items) < 2:
      return []

    candidates = [
      Candidate(
        key=it.key,
        sql=it.representative_sql,
        exec_hash=it.exec_hash,
        weight=it.weight,
      )
      for it in items
    ]
    drafts = build_token_question_drafts(candidates)
    questions: list[Question] = []
    for d in drafts:
      options = [
        OptionSpec(
          label=label,
          world_hashes=exec_hashes,
          item_keys=keys,
          representative_sql=rep,
        )
        for label, keys, exec_hashes, rep in d.options
      ]
      from experiment.pipeline.openworld.oracle_hint import scope_from_slot_id
      qid = f"token:{d.slot_id}"
      questions.append(Question(
        options=options,
        source=qid,
        metadata={"slot_id": d.slot_id},
        scope=scope_from_slot_id(d.slot_id),
        qid=qid,
      ))
    return questions


class _Selector(ABC):
  @abstractmethod
  def select(
      self,
      pool: list[Question],
      items: list[InteractionItem],
      history: list,
  ) -> Question:
    ...


class RandomSelector(_Selector):
  def __init__(self, seed: int = 0):
    self._rng = random.Random(seed)

  def select(self, pool, items, history):
    return self._rng.choice(pool)


class MaxProbSelector(_Selector):
  def select(self, pool, items, history):
    total = sum(it.weight for it in items) or 1

    def score(q: Question) -> float:
      best = 0
      for opt in q.options:
        keys = _option_keys(opt)
        s = sum(it.weight for it in items if it.key in keys)
        best = max(best, s)
      return best / total

    return max(pool, key=score)


class EIGSelector(_Selector):
  def select(self, pool, items, history):
    best_q = pool[0]
    best_eig = -1.0
    for q in pool:
      eig = compute_eig(q, items)
      if eig > best_eig:
        best_eig = eig
        best_q = q
    best_q.metadata = {**best_q.metadata, "eig_value": best_eig}
    return best_q


def _prob_answer_given_item(
    q: Question,
    item_key: str,
    outcome: int,
) -> float:
  """P(outcome | item key); outcome in option indices or -1."""
  n_opts = len(q.options)
  if outcome == -1:
    return 0.0
  if outcome < 0 or outcome >= n_opts:
    return 0.0
  keys = _option_keys(q.options[outcome])
  return 1.0 if item_key in keys else 0.0


def compute_eig_with_other(
    question: Question,
    items: list[InteractionItem],
    epsilon_0: float,
    alpha: float = 0.9,
) -> float:
  """EIG on augmented W ∪ {w_other} with open-world answer -1."""
  if not items:
    return 0.0
  total = sum(it.weight for it in items)
  if total <= 0:
    return 0.0

  n_opts = len(question.options)
  outcomes = list(range(n_opts)) + [-1]

  def _p_item(o: int, key: str) -> float:
    return _prob_answer_given_item(question, key, o)

  p_w = {it.key: (1.0 - epsilon_0) * (it.weight / total) for it in items}
  p_other = epsilon_0

  p_o: dict[int, float] = {}
  for o in outcomes:
    mass = p_other * (alpha if o == -1 else (1.0 - alpha) / max(n_opts, 1))
    for it in items:
      mass += p_w[it.key] * _p_item(o, it.key)
    p_o[o] = mass

  h_o = 0.0
  for o, po in p_o.items():
    if po > 0:
      h_o -= po * math.log2(po)

  cond = 0.0
  # w_other
  h_other = 0.0
  for o in outcomes:
    po = alpha if o == -1 else (1.0 - alpha) / max(n_opts, 1)
    if po > 0:
      h_other -= po * math.log2(po)
  cond += p_other * h_other

  for it in items:
    inner = 0.0
    for o in outcomes:
      po = _p_item(o, it.key)
      if po > 0:
        inner -= po * math.log2(po)
    cond += p_w[it.key] * inner

  return h_o - cond


class EIGSelectorWithOther(_Selector):
  def __init__(self, epsilon_0: float, alpha: float = 0.9):
    self.epsilon_0 = epsilon_0
    self.alpha = alpha

  def select(self, pool, items, history):
    best_q = pool[0]
    best_eig = -1.0
    for q in pool:
      eig = compute_eig_with_other(q, items, self.epsilon_0, self.alpha)
      if eig > best_eig:
        best_eig = eig
        best_q = q
    best_q.metadata = {**best_q.metadata, "eig_value": best_eig}
    return best_q


def build_pool(
    name: str,
    db_path: str | Path | None = None,
    *,
    use_nl_rendering: bool = False,
    llm_client: Any | None = None,
    question_text: str = "",
):
  if name == "cluster":
    return ClusterPool()
  if name == "atomic":
    return AtomicPool(
        db_path=db_path,
        use_nl_rendering=use_nl_rendering,
        llm_client=llm_client,
        question_text=question_text,
    )
  if name == "token":
    return TokenDiffPool()
  raise ValueError(f"unknown pool {name!r}")


def build_selector(
    name: str,
    seed: int = 0,
    *,
    epsilon_0: float = 0.56,
    alpha: float = 0.9,
    with_other: bool = False,
) -> _Selector:
  if name == "random":
    return RandomSelector(seed=seed)
  if name == "max_prob":
    return MaxProbSelector()
  if name == "eig":
    if with_other:
      return EIGSelectorWithOther(epsilon_0=epsilon_0, alpha=alpha)
    return EIGSelector()
  raise ValueError(f"unknown selector {name!r}")
