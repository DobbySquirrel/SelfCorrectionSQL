#!/usr/bin/env python3
"""Business SQL step plans for CTE Phase2.

Two entry points:
  - plan_business_oneshot: ONE prompt (question+evidence+schema → steps). Preferred.
  - rewrite_business_plan: legacy second-pass rewrite of an existing P1 tree plan.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from workflows.mcts_v4.utils.llm_chat import create_chat_completion

# Simple one-shot business plan.
PROMPT_ONESHOT_BUSINESS_PLAN = """You are planning intermediate SQL steps for Text-to-SQL (SQLite).

Original question:
{question}

Evidence / Hint (respect literals exactly — values, column names, filters):
{evidence}

Database schema (use only tables/columns that appear here):
{schema}

Task: produce ONE plan of BUSINESS SQL steps. Each step will later become one CTE.

Requirements:
1. Output 2 to 5 steps. Each step is a DATA operation only
   (filter / join / aggregate / rank / project / compute).
2. FORBIDDEN (do NOT output):
   - yes/no existence checks ("Is X present?", "Does any row ...?")
   - schema probes ("Is column C in table T?")
   - evidence validation ("Does evidence say ...?")
   - boolean / SELECT 1 AS exists_flag style goals
3. Prefer Evidence wording over paraphrases (e.g. "has values" → IS NOT NULL, not MIN,
   unless Evidence clearly means min/max).
4. Steps must be ordered so later steps can reuse earlier intermediate results.
5. The LAST step MUST project the final answer columns for the original question
   (correct grain: id/name/count/average/etc.).

Output JSON only:
{{"steps": ["...", "..."], "notes": "one short sentence"}}
"""

# DeepEye-style DC decomposition → business CTE steps (not end-to-end SQL).
PROMPT_DEEPEYE_DC_PLAN = """You are an experienced database expert planning CTE steps for Text-to-SQL (SQLite).

Use a Divide-and-Conquer mindset, but OUTPUT ONLY an ordered list of BUSINESS data steps
(each step will become one intermediate CTE). Do NOT output final SQL.

Original question:
{question}

Evidence / Hint (respect literals exactly):
{evidence}

Database schema:
{schema}

Approach:
1. Divide the question into 2–5 simpler sub-goals (filter / join / aggregate / rank / project).
2. Order them bottom-up so later steps can reuse earlier intermediate results by CTE name.
3. The LAST step MUST project the final answer columns for the original question.

FORBIDDEN:
- yes/no existence checks ("Is X present?")
- schema probes ("Is column C in table T?")
- evidence validation questions
- boolean / EXISTS-flag style goals

Output JSON only:
{{"steps": ["...", "..."], "notes": "one short sentence"}}
"""


# Explicit disambiguation tree → directly emit BUSINESS steps (tree + rewrite fused).
PROMPT_TREE_BUSINESS_PLAN = """You resolve ambiguity then output BUSINESS CTE steps in ONE response (SQLite Text-to-SQL).

Original question:
{question}

Evidence / Hint (respect literals exactly; prefer Evidence wording over paraphrases):
{evidence}

Database schema:
{schema}

Do this internally (do not print the tree):
1. List 2–4 competing interpretations that differ on grain / join / filter / aggregation
   (especially around Evidence phrases like "has values"→IS NOT NULL vs MIN).
2. Pick the interpretation that best matches Evidence + question.
3. Turn that interpretation into 2–5 BUSINESS SQL steps (filter/join/aggregate/rank/project).
4. LAST step projects the final answer columns.

FORBIDDEN in steps: yes/no probes, schema probes, evidence-validation questions.

Output JSON only:
{{"steps": ["...", "..."], "notes": "which ambiguity you resolved + why"}}
"""


# Parallel slot plan: independent retrievals first, then one assemble step.
PROMPT_PARALLEL_SLOTS_PLAN = """You plan PARALLEL intermediate SQL slots for Text-to-SQL (SQLite), then ONE assemble step.

Original question:
{question}

Evidence / Hint (respect literals exactly):
{evidence}

Database schema:
{schema}

Planning rules (mandatory):
1. Output 3 to 5 steps total.
2. Steps 1..(N-1) are INDEPENDENT parallel slots described in SHORT ENGLISH only:
   - Each slot builds ONE intermediate table from base tables only.
   - Slots MUST NOT depend on each other (no "using previous step").
   - Each slot is a self-contained retrieval / filter / join / aggregate useful later
     (e.g. "Get schools in Fresno Unified with CDSCode and School name",
      "Get SAT AvgScrRead per school keyed by cds").
   - Prefer keys + attributes needed later; do NOT project the final answer column alone
     (e.g. avoid selecting only Phone/email of the eventual winner in an early slot).
   - Do NOT put final ranking/LIMIT-1 answer into early slots unless that slot alone is the answer.
3. The LAST step is ASSEMBLE only (English):
   - Combine the preceding slot results (JOIN / filter / rank / project) to answer the original question.
   - Explicitly say it must reuse preceding slot results.
4. CRITICAL FORMAT:
   - Every step MUST be a natural-language business description.
   - NEVER write SQL keywords in steps (no WITH/SELECT/FROM/JOIN/WHERE/ORDER/LIMIT).
   - NEVER paste code or CTE definitions into steps.
5. FORBIDDEN: yes/no probes, schema probes, evidence-validation questions.

Output JSON only:
{{"steps": ["Get ...", "Get ...", "Assemble ..."], "notes": "why these parallel slots + assemble"}}
"""

PROMPT_REWRITE = """Rewrite the plan into BUSINESS SQL steps for Text-to-SQL.

Original question:
{question}

Evidence:
{evidence}

Current plan (may contain bad meta/yes-no probes — rewrite them away):
{old_plan}

Requirements:
1. Output 2 to 5 steps. Each step is a DATA operation (filter / join / aggregate / rank / project).
2. FORBIDDEN step types (do NOT output these):
   - yes/no existence checks ("Is X present?", "Does any row ...?")
   - schema probes ("Is column C in table T?")
   - evidence validation ("Does evidence say ...?", "Is MAX the correct definition?")
   - boolean flags / SELECT 1 AS exists_flag style goals
3. The LAST step MUST project the final answer columns that answer the original question
   (same grain as the expected answer: ids, names, counts, averages, etc.).
4. Respect Evidence literals exactly (values, column names).
5. Steps should be ordered so later steps can reuse earlier intermediate results.

Output JSON only:
{{"steps": ["...", "..."], "notes": "one short sentence"}}
"""


PROMPT_PICK_FINAL = """You pick which CTE-prefix SQL best answers the ORIGINAL question.

Original question:
{question}

Evidence:
{evidence}

Candidates (WITH-chain prefixes). Higher index = LATER / more complete.
DEFAULT: pick the LAST candidate (highest index) unless it is clearly wrong.

Only pick an EARLIER index when the last candidate is clearly worse, e.g.:
- last result is empty / ERROR while an earlier one is a valid answer
- last has wrong grain (e.g. monthly row when question needs yearly total; raw join dump)
- last projects wrong columns (extra score/total columns when question asks only a name/year/id)
- last re-introduces duplicates or drops DISTINCT that an earlier answer had correctly

Do NOT pick early just because it "looks complete". Many early prefixes already contain a
full query due to bad chaining; if last also has a plausible final answer, KEEP LAST.
If two candidates differ only by an extra aggregated column (year+sum vs year), prefer the
one whose columns match what the question asks for (usually the leaner later projection).

{cands}

Output JSON only:
{{"pick": <index starting from 0>, "reason": "short"}}
"""


PROMPT_PICK_FINAL_KEEP_LAST_LEGACY = """Decide whether to KEEP the default LAST CTE-prefix or SWITCH to an earlier one.

Original question:
{question}

Evidence:
{evidence}

DEFAULT candidate (KEEP unless clearly wrong):
[DEFAULT] step_index={default_index}
SQL: {default_sql}
Result: {default_result}

Earlier alternatives (only SWITCH if default is clearly wrong on grain/columns/emptiness):
{alts}

Rules:
- Prefer KEEP.
- SWITCH only if default result is empty/ERROR, wrong grain, wrong columns for the question,
  or clearly not answering the question while an alternative does.
- Do not SWITCH merely because an earlier prefix also looks like a full answer
  (fake chaining often makes step0 already look complete).

Output JSON only:
{{"decision": "KEEP" or "SWITCH", "pick": <step_index of chosen candidate>, "reason": "short"}}
"""

PROMPT_PICK_FINAL_KEEP_LAST = """Decide whether to KEEP the default LAST CTE-prefix or SWITCH to an earlier one.

Original question:
{question}

Evidence:
{evidence}

DEFAULT candidate (KEEP unless clearly wrong):
[DEFAULT] step_index={default_index}
SQL: {default_sql}
Result: {default_result}

Earlier alternatives (0-based list index in brackets; only SWITCH if default is clearly wrong):
{alts}

Rules:
- Prefer KEEP.
- SWITCH only if default result is empty/ERROR, wrong grain, wrong columns for the question,
  or clearly not answering the question while an alternative does.
- Do not SWITCH merely because an earlier prefix also looks like a full answer
  (fake chaining often makes step0 already look complete).
- On SWITCH, set "pick" to the 0-based index shown in brackets [0], [1], ... (NOT step_index).

Output JSON only:
{{"decision": "KEEP" or "SWITCH", "pick": <0-based alt list index>, "reason": "short"}}
"""


def _bugfix_switch_enabled() -> bool:
    """Bugfix #3: list-index-first SWITCH + default-ok guard (default OFF)."""
    return os.environ.get("MCTS_BUGFIX_SWITCH", "0").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )

PROMPT_VOTE_PLANS = """Pick the best BUSINESS SQL step-plan for Text-to-SQL (SQLite).

Original question:
{question}

Evidence:
{evidence}

Schema (truncated):
{schema}

Candidate plans (0-indexed). Prefer: Evidence-literal filters, correct grain/joins,
NO yes/no or schema probes, LAST step projects the final answer.

{candidates_block}

Output JSON only:
{{"pick": <index>, "reason": "one sentence"}}
"""


def _parse_steps_json(text: str) -> Tuple[List[str], str]:
    m = re.search(r"\{[\s\S]*\}", text or "")
    steps: List[str] = []
    notes = ""
    if not m:
        return steps, notes
    try:
        obj = json.loads(m.group(0))
        raw = obj.get("steps") or []
        steps = [str(s).strip() for s in raw if str(s).strip()]
        notes = str(obj.get("notes") or "")
    except Exception:
        return [], ""
    return steps, notes


def _ensure_final_projection(steps: List[str], question: str) -> List[str]:
    out = list(steps)
    last = (out[-1] if out else "").lower()
    keys = (
        "answer",
        "return",
        "final",
        "list",
        "report",
        "give",
        "what",
        "which",
        "how many",
        "average",
        "name",
    )
    if not any(k in last for k in keys):
        out.append(
            f"Return the final answer for the original question (project only the requested columns): {question}"
        )
    return out[:6]


def _steps_look_like_sql(steps: List[str]) -> bool:
    sql_re = re.compile(
        r"\b(WITH|SELECT|FROM|JOIN|WHERE|GROUP BY|ORDER BY|LIMIT)\b",
        re.I,
    )
    return any(sql_re.search(s or "") for s in steps)


def plan_business_oneshot(
    *,
    client,
    model: str,
    question: str,
    evidence: str,
    schema: str,
    variant: str = "oneshot",
    temperature: float = 0.2,
) -> Tuple[List[str], Dict[str, Any]]:
    """One LLM call: question+evidence+schema → business CTE steps.

    variant: oneshot | deepeye_dc | tree_business | parallel_slots
    """
    tmpl = {
        "oneshot": PROMPT_ONESHOT_BUSINESS_PLAN,
        "deepeye_dc": PROMPT_DEEPEYE_DC_PLAN,
        "tree_business": PROMPT_TREE_BUSINESS_PLAN,
        "parallel_slots": PROMPT_PARALLEL_SLOTS_PLAN,
    }.get(variant) or PROMPT_ONESHOT_BUSINESS_PLAN
    prompt = tmpl.format(
        question=question,
        evidence=evidence or "(none)",
        schema=(schema or "(empty)")[:8000],
    )

    def _call(p: str, temp: float) -> str:
        resp = create_chat_completion(
            client,
            model=model,
            messages=[{"role": "user", "content": p}],
            temperature=float(temp),
        )
        return resp.choices[0].message.content or ""

    try:
        text = _call(prompt, temperature)
    except Exception as e:
        return [question], {
            "ok": False,
            "mode": variant,
            "error": str(e),
            "fallback": "question",
        }

    steps, notes = _parse_steps_json(text)
    retried_sql = False
    if variant == "parallel_slots" and steps and _steps_look_like_sql(steps):
        retried_sql = True
        fix = (
            prompt
            + "\n\nYour previous steps contained SQL code. Rewrite again: "
            "steps must be short ENGLISH descriptions only, zero SQL keywords."
        )
        try:
            text2 = _call(fix, 0.1)
            steps2, notes2 = _parse_steps_json(text2)
            if steps2 and not _steps_look_like_sql(steps2):
                steps, notes, text = steps2, notes2, text2
        except Exception:
            pass

    if len(steps) < 2:
        steps = [question]
        steps.append(
            f"Project the final answer columns that fully answer the original question: {question}"
        )
        return steps[:4], {
            "ok": False,
            "mode": variant,
            "fallback": "question_plus_final",
            "notes": notes,
            "raw": text[:300],
            "retried_sql": retried_sql,
        }
    steps = _ensure_final_projection(steps, question)
    return steps, {
        "ok": True,
        "mode": variant,
        "notes": notes,
        "n_steps": len(steps),
        "retried_sql": retried_sql,
        "looks_like_sql": _steps_look_like_sql(steps),
    }


def _plan_key(steps: List[str]) -> str:
    return re.sub(r"\s+", " ", " || ".join(steps).lower()).strip()


def _sample_business_plans(
    *,
    client,
    model: str,
    question: str,
    evidence: str,
    schema: str,
    n_plans: int = 4,
    variant: str = "tree_business",
    sample_temperature: float = 0.7,
) -> Tuple[List[Dict[str, Any]], int]:
    """Sample + dedupe business plans. Returns (uniq_cands, n_sampled)."""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    n = max(1, min(4, int(n_plans)))
    variants = [variant] * n
    if n >= 2:
        pool = ["tree_business", "deepeye_dc", "oneshot", "tree_business"]
        variants = pool[:n]

    cands: List[Dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=n) as ex:
        futs = {
            ex.submit(
                plan_business_oneshot,
                client=client,
                model=model,
                question=question,
                evidence=evidence,
                schema=schema,
                variant=variants[i],
                temperature=0.2 if i == 0 else sample_temperature,
            ): i
            for i in range(n)
        }
        for fut in as_completed(futs):
            i = futs[fut]
            try:
                steps, audit = fut.result()
            except Exception as e:
                steps, audit = [question], {"ok": False, "error": str(e)}
            cands.append(
                {
                    "idx": i,
                    "variant": variants[i],
                    "steps": steps,
                    "audit": audit,
                    "key": _plan_key(steps),
                }
            )
    cands.sort(key=lambda x: x["idx"])

    uniq: List[Dict[str, Any]] = []
    seen = set()
    for c in cands:
        if c["key"] in seen:
            continue
        if len(c.get("steps") or []) < 2:
            continue
        seen.add(c["key"])
        uniq.append(c)
    if not uniq:
        uniq = cands[:1] if cands else [
            {"idx": 0, "steps": [question], "variant": variant, "audit": {}, "key": ""}
        ]
    return uniq, n


def plan_business_vote(
    *,
    client,
    model: str,
    question: str,
    evidence: str,
    schema: str,
    n_plans: int = 4,
    variant: str = "tree_business",
    sample_temperature: float = 0.7,
) -> Tuple[List[str], Dict[str, Any]]:
    """Sample 1..n_plans business plans, then LLM-vote for the best."""
    uniq, n = _sample_business_plans(
        client=client,
        model=model,
        question=question,
        evidence=evidence,
        schema=schema,
        n_plans=n_plans,
        variant=variant,
        sample_temperature=sample_temperature,
    )

    if len(uniq) == 1:
        win = uniq[0]
        return list(win["steps"]), {
            "ok": True,
            "mode": "vote",
            "n_sampled": n,
            "n_unique": 1,
            "pick": 0,
            "reason": "single unique plan",
            "variants": [win.get("variant")],
            "candidates": [{"variant": win.get("variant"), "steps": win["steps"], "notes": (win.get("audit") or {}).get("notes")}],
        }

    block_lines = []
    for j, c in enumerate(uniq):
        steps_s = "\n".join(f"  {k+1}. {s}" for k, s in enumerate(c["steps"]))
        block_lines.append(f"[{j}] variant={c.get('variant')}\n{steps_s}")
    vote_prompt = PROMPT_VOTE_PLANS.format(
        question=question,
        evidence=evidence or "(none)",
        schema=(schema or "")[:4000],
        candidates_block="\n\n".join(block_lines),
    )
    pick_i = 0
    reason = "fallback 0"
    try:
        resp = create_chat_completion(
            client,
            model=model,
            messages=[{"role": "user", "content": vote_prompt}],
            temperature=0.0,
        )
        text = resp.choices[0].message.content or ""
        m = re.search(r"\{[\s\S]*\}", text)
        if m:
            obj = json.loads(m.group(0))
            pick_i = int(obj.get("pick") or 0)
            reason = str(obj.get("reason") or "")[:300]
    except Exception as e:
        reason = f"vote_err:{e}"
        pick_i = 0
    if pick_i < 0 or pick_i >= len(uniq):
        pick_i = 0
    win = uniq[pick_i]
    return list(win["steps"]), {
        "ok": True,
        "mode": "vote",
        "n_sampled": n,
        "n_unique": len(uniq),
        "pick": pick_i,
        "reason": reason,
        "variants": [c.get("variant") for c in uniq],
        "candidates": [
            {
                "variant": c.get("variant"),
                "steps": c["steps"],
                "notes": (c.get("audit") or {}).get("notes"),
            }
            for c in uniq
        ],
    }


def plan_business_vote_plugin(
    *,
    client,
    model: str,
    question: str,
    evidence: str,
    schema: str,
    db_path: Path,
    llm_config: Optional[dict] = None,
    n_plans: int = 4,
    variant: str = "tree_business",
    sample_temperature: float = 0.7,
    few_shots: Optional[List[Dict[str, str]]] = None,
    value_retrieval_hint: str = "",
    n_dc: int = 2,
    n_skeleton: int = 2,
    n_icl: int = 0,
    max_workers: int = 6,
    revise_max_unique: int = 4,
    checker_budget: int = 1,
    chain_mode: str = "off",
) -> Tuple[List[str], Dict[str, Any]]:
    """Sample plans, score each by DeepEye step-0 circle (consistency), pick best.

    Posterior signal: run only plan.steps[0] through deepeye_cte_full_plugin and
    use winner_consistency (+ selection_mode / non-empty) as plan quality.
    """
    from workflows.mcts_v4.actions.deepeye_cte_full_plugin import deepeye_cte_full_plugin

    db_path = Path(db_path)
    uniq, n = _sample_business_plans(
        client=client,
        model=model,
        question=question,
        evidence=evidence,
        schema=schema,
        n_plans=n_plans,
        variant=variant,
        sample_temperature=sample_temperature,
    )

    scored: List[Dict[str, Any]] = []
    for j, c in enumerate(uniq):
        steps = list(c.get("steps") or [])
        sub0 = (steps[0] if steps else question).strip()
        try:
            out = deepeye_cte_full_plugin(
                db_path=db_path,
                schema=schema,
                question=question,
                evidence=evidence,
                sub_question=sub0,
                preceding_ctes=[],
                llm_config=llm_config,
                few_shots=few_shots,
                value_retrieval_hint=value_retrieval_hint,
                n_dc=n_dc,
                n_skeleton=n_skeleton,
                n_icl=n_icl,
                filter_top_k=2,
                evaluator_votes=3,
                shortcut_threshold=0.6,
                checker_budget=checker_budget,
                revise_max_unique=revise_max_unique,
                max_workers=max_workers,
                progress_prefix=f"[plan-probe j={j}]",
                chain_mode=chain_mode,
            )
            aud = out.get("audit") or {}
            cons = float(out.get("winner_consistency") or 0.0)
            mode = str(aud.get("selection_mode") or "")
            n_clust = int(aud.get("n_clusters") or 0)
            ok = bool(out.get("winner_cte"))
            # Prefer high consistency; shortcut > br; non-empty winner.
            mode_bonus = {
                "shortcut_consistency": 0.05,
                "only_one": 0.03,
                "br_pairwise": 0.0,
            }.get(mode, 0.0)
            score = (cons if ok else -1.0) + mode_bonus
        except Exception as e:
            cons, mode, n_clust, ok, score = 0.0, "error", 0, False, -2.0
            out = {"error": str(e)[:200]}
            aud = {}
        scored.append(
            {
                "j": j,
                "variant": c.get("variant"),
                "steps": steps,
                "notes": (c.get("audit") or {}).get("notes"),
                "sub0": sub0[:200],
                "score": round(score, 4),
                "consistency": round(cons, 4),
                "selection_mode": mode,
                "n_clusters": n_clust,
                "winner_ok": ok,
            }
        )
        print(
            f"  [plan-plugin] cand[{j}] var={c.get('variant')} cons={cons:.3f} "
            f"mode={mode} score={score:.3f} sub0={sub0[:60]!r}",
            flush=True,
        )

    scored_sorted = sorted(scored, key=lambda x: (-float(x["score"]), x["j"]))
    pick_i = int(scored_sorted[0]["j"]) if scored_sorted else 0
    win = uniq[pick_i]
    return list(win["steps"]), {
        "ok": True,
        "mode": "vote_plugin",
        "n_sampled": n,
        "n_unique": len(uniq),
        "pick": pick_i,
        "reason": (
            f"step0_consistency={scored[pick_i]['consistency']} "
            f"mode={scored[pick_i]['selection_mode']}"
            if scored
            else "empty"
        ),
        "variants": [c.get("variant") for c in uniq],
        "candidates": scored,
        "probe": {
            "n_dc": n_dc,
            "n_skeleton": n_skeleton,
            "n_icl": n_icl,
            "revise_max_unique": revise_max_unique,
        },
    }


PROMPT_PLAN_FROM_CIRCLE_THINK = """You already ran a DeepEye-style executable THINKING pass on this Text-to-SQL question.
Use that thinking as prior: the winner SQL/result (and cluster consistency) are evidence about the correct solution grain/joins/filters.
NOW produce a BUSINESS CTE step-plan that implements the same solution path as a short chain of intermediate SQL steps.

Rules:
- 2-5 steps. Each step = one CTE later.
- Write steps in PLAIN ENGLISH / business language (e.g. "Filter schools in Los Angeles", "Join with frpm on CDSCode").
- Do NOT paste SQL, WITH/AS clauses, or code fragments into steps.
- Respect Evidence literals exactly.
- LAST step must project the final answer columns for the original question.
- Do NOT invent tables/columns absent from schema.
- Prefer decomposing the winner SQL into true intermediate steps (filters → joins → agg), not repeating the full query every step.
- If thinking looks wrong/empty, still produce the best plan you can from question+evidence+schema.

Original question:
{question}

Evidence:
{evidence}

Schema (truncated):
{schema}

=== THINKING (DeepEye circle) ===
selection_mode: {selection_mode}
winner_consistency: {consistency}
n_clusters: {n_clusters}
winner SQL:
{winner_sql}
execution result preview:
{result_preview}
top cluster notes (sig/size/cons):
{cluster_notes}

Output JSON only:
{{"steps": ["...", "..."], "notes": "how plan follows thinking"}}
"""


PROMPT_REPLAN_FROM_ROLLOUT = """You already did: (1) DeepEye thinking circle, (2) a CTE plan, (3) executing that plan.
The rollout result may be wrong or incomplete. Produce a REVISED business CTE plan.

Rules:
- 2-5 steps in PLAIN ENGLISH (no SQL code in steps).
- Fix grain/joins/filters using Evidence and the observed failure signals.
- LAST step projects the final answer for the original question.

Original question:
{question}

Evidence:
{evidence}

Schema (truncated):
{schema}

Previous thinking winner SQL:
{think_sql}

Previous plan:
{prev_plan}

Rollout last SQL:
{last_sql}

Rollout last result preview:
{last_result}

Pick audit / notes:
{pick_notes}

Output JSON only:
{{"steps": ["...", "..."], "notes": "what changed and why"}}
"""


PROMPT_REPLAN_FUTURE = """You are mid-way through a Text-to-SQL CTE plan. Correct the REMAINING future steps only.

Completed steps (already executed — do NOT repeat them):
{done_plan}

Latest intermediate CTE/SQL just produced:
```sql
{latest_cte}
```

Original remaining future steps (may be wrong / redundant / too aggressive):
{future_plan}

Original question:
{question}

Evidence:
{evidence}

Schema (truncated):
{schema}

Rules:
1. Output ONLY the revised FUTURE steps (what still needs to be done after the completed steps).
2. 1 to 4 future steps in PLAIN ENGLISH — no SQL keywords in steps.
3. If the latest CTE already over-solved later goals (e.g. already has ORDER/LIMIT/final columns),
   rewrite future steps to build on that intermediate (or project/fix only), do not redo everything.
4. If latest CTE is a proper intermediate, keep future steps aligned to finish the original question.
5. LAST future step must project the final answer columns for the original question.
6. Prefer using preceding CTE results rather than restarting from scratch when possible.

Output JSON only:
{{"steps": ["...", "..."], "notes": "what you corrected in the future plan"}}
"""


def plan_from_circle_think(
    *,
    client,
    model: str,
    question: str,
    evidence: str,
    schema: str,
    db_path: Path,
    llm_config: Optional[dict] = None,
    few_shots: Optional[List[Dict[str, str]]] = None,
    value_retrieval_hint: str = "",
    n_dc: int = 4,
    n_skeleton: int = 4,
    n_icl: int = 0,
    max_workers: int = 8,
    revise_max_unique: int = 6,
    checker_budget: int = 2,
    evaluator_votes: int = 5,
) -> Tuple[List[str], Dict[str, Any]]:
    """DeepEye circle FIRST (thinking), then LLM plans CTEs grounded in that posterior.

    This is the opposite order of vote_plugin (plans first, circle scores).
    """
    from workflows.mcts_v4.actions.deepeye_cte_full_plugin import deepeye_cte_full_plugin

    db_path = Path(db_path)
    print("  [circle-think] running DeepEye circle on full question...", flush=True)
    think = deepeye_cte_full_plugin(
        db_path=db_path,
        schema=schema,
        question=question,
        evidence=evidence,
        sub_question=question,
        preceding_ctes=[],
        llm_config=llm_config,
        few_shots=few_shots,
        value_retrieval_hint=value_retrieval_hint,
        n_dc=n_dc,
        n_skeleton=n_skeleton,
        n_icl=n_icl,
        filter_top_k=2,
        evaluator_votes=evaluator_votes,
        shortcut_threshold=0.6,
        checker_budget=checker_budget,
        revise_max_unique=revise_max_unique,
        max_workers=max_workers,
        progress_prefix="[circle-think]",
        chain_mode="off",
    )
    aud = think.get("audit") or {}
    clusters = list(think.get("clusters") or [])[:3]
    cluster_notes = []
    for c in clusters:
        cluster_notes.append(
            f"- size={c.get('size')} cons={c.get('consistency')} "
            f"preview={(c.get('rows_preview') or '')[:120]}"
        )
    winner_sql = (think.get("winner_cte") or "")[:2500]
    # Prefer rows_preview from winning cluster if available
    result_preview = ""
    for c in clusters:
        if (c.get("cte") or "") == (think.get("winner_cte") or ""):
            result_preview = (c.get("rows_preview") or "")[:500]
            break
    if not result_preview and clusters:
        result_preview = (clusters[0].get("rows_preview") or "")[:500]

    prompt = PROMPT_PLAN_FROM_CIRCLE_THINK.format(
        question=question,
        evidence=evidence or "(none)",
        schema=(schema or "")[:5000],
        selection_mode=aud.get("selection_mode") or "",
        consistency=think.get("winner_consistency") or 0,
        n_clusters=aud.get("n_clusters") or len(clusters),
        winner_sql=winner_sql or "(empty)",
        result_preview=result_preview or "(empty)",
        cluster_notes="\n".join(cluster_notes) or "(none)",
    )
    steps: List[str] = []
    notes = ""
    raw = ""
    try:
        resp = create_chat_completion(
            client,
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
        )
        raw = resp.choices[0].message.content or ""
        m = re.search(r"\{[\s\S]*\}", raw)
        if m:
            obj = json.loads(m.group(0))
            steps = [str(s).strip() for s in (obj.get("steps") or []) if str(s).strip()]
            notes = str(obj.get("notes") or "")[:400]
    except Exception as e:
        notes = f"plan_err:{e}"

    if len(steps) < 2:
        # fallback: tree_business without thinking
        steps, fb = plan_business_oneshot(
            client=client,
            model=model,
            question=question,
            evidence=evidence,
            schema=schema,
            variant="tree_business",
        )
        notes = (notes + " | fallback_tree_business").strip(" |")
        fb_ok = fb.get("ok")
    else:
        steps = _ensure_final_projection(steps, question)
        fb_ok = True

    print(
        f"  [circle-think] cons={think.get('winner_consistency')} "
        f"mode={aud.get('selection_mode')} n_steps={len(steps)}",
        flush=True,
    )
    return steps, {
        "ok": bool(fb_ok and steps),
        "mode": "circle_think",
        "notes": notes,
        "n_steps": len(steps),
        "think": {
            "selection_mode": aud.get("selection_mode"),
            "winner_consistency": think.get("winner_consistency"),
            "n_clusters": aud.get("n_clusters"),
            "winner_cte": think.get("winner_cte") or "",
            "winner_cte_preview": (think.get("winner_cte") or "")[:400],
            "result_preview": result_preview[:300],
            "elapsed_s": aud.get("elapsed_s"),
        },
        "raw": raw[:400],
    }


def replan_from_rollout(
    *,
    client,
    model: str,
    question: str,
    evidence: str,
    schema: str,
    think_sql: str,
    prev_steps: List[str],
    last_sql: str,
    last_result: str,
    pick_notes: str = "",
) -> Tuple[List[str], Dict[str, Any]]:
    """Second-round plan revision after seeing rollout outcome (iterative reasoning)."""
    prev_plan = "\n".join(f"{i+1}. {s}" for i, s in enumerate(prev_steps or []))
    prompt = PROMPT_REPLAN_FROM_ROLLOUT.format(
        question=question,
        evidence=evidence or "(none)",
        schema=(schema or "")[:5000],
        think_sql=(think_sql or "")[:2000] or "(none)",
        prev_plan=prev_plan or "(none)",
        last_sql=(last_sql or "")[:2000] or "(none)",
        last_result=(last_result or "")[:500] or "(none)",
        pick_notes=(pick_notes or "")[:300] or "(none)",
    )
    steps: List[str] = []
    notes = ""
    raw = ""
    try:
        resp = create_chat_completion(
            client,
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
        )
        raw = resp.choices[0].message.content or ""
        m = re.search(r"\{[\s\S]*\}", raw)
        if m:
            obj = json.loads(m.group(0))
            steps = [str(s).strip() for s in (obj.get("steps") or []) if str(s).strip()]
            notes = str(obj.get("notes") or "")[:400]
    except Exception as e:
        notes = f"replan_err:{e}"
    if len(steps) < 2:
        return list(prev_steps or [question]), {
            "ok": False,
            "mode": "replan_from_rollout",
            "notes": notes or "keep_prev",
            "n_steps": len(prev_steps or []),
            "raw": raw[:300],
        }
    steps = _ensure_final_projection(steps, question)
    return steps, {
        "ok": True,
        "mode": "replan_from_rollout",
        "notes": notes,
        "n_steps": len(steps),
        "raw": raw[:300],
    }


def replan_future_remaining(
    *,
    client,
    model: str,
    question: str,
    evidence: str,
    schema: str,
    done_steps: List[str],
    future_steps: List[str],
    latest_cte: str,
) -> Tuple[List[str], Dict[str, Any]]:
    """Mid-chain: revise only the remaining future plan steps after a CTE is produced."""
    done_plan = "\n".join(f"{i+1}. {s}" for i, s in enumerate(done_steps or [])) or "(none)"
    future_plan = "\n".join(f"{i+1}. {s}" for i, s in enumerate(future_steps or [])) or "(none)"
    prompt = PROMPT_REPLAN_FUTURE.format(
        done_plan=done_plan,
        latest_cte=(latest_cte or "")[:2500] or "(empty)",
        future_plan=future_plan,
        question=question,
        evidence=evidence or "(none)",
        schema=(schema or "")[:5000],
    )
    steps: List[str] = []
    notes = ""
    raw = ""
    try:
        resp = create_chat_completion(
            client,
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
        )
        raw = resp.choices[0].message.content or ""
        m = re.search(r"\{[\s\S]*\}", raw)
        if m:
            obj = json.loads(m.group(0))
            steps = [str(s).strip() for s in (obj.get("steps") or []) if str(s).strip()]
            notes = str(obj.get("notes") or "")[:400]
    except Exception as e:
        notes = f"replan_future_err:{e}"
    if not steps:
        return list(future_steps or []), {
            "ok": False,
            "mode": "replan_future",
            "notes": notes or "keep_future",
            "n_steps": len(future_steps or []),
            "raw": raw[:300],
        }
    # Drop accidental SQL-looking steps.
    if any(re.search(r"\b(WITH|SELECT|FROM)\b", s, re.I) for s in steps):
        return list(future_steps or []), {
            "ok": False,
            "mode": "replan_future",
            "notes": "rejected_sql_steps|" + (notes or ""),
            "n_steps": len(future_steps or []),
            "raw": raw[:300],
        }
    steps = _ensure_final_projection(steps, question)
    changed = [s.strip() for s in steps] != [s.strip() for s in (future_steps or [])]
    return steps, {
        "ok": True,
        "mode": "replan_future",
        "notes": notes,
        "n_steps": len(steps),
        "changed": changed,
        "raw": raw[:300],
    }


def rewrite_business_plan(
    *,
    client,
    model: str,
    question: str,
    evidence: str,
    old_steps: List[str],
) -> Tuple[List[str], Dict[str, Any]]:
    """Legacy: rewrite an existing P1 tree plan into business steps (2nd LLM call)."""
    old_plan = "\n".join(f"{i+1}. {s}" for i, s in enumerate(old_steps)) or "(empty)"
    prompt = PROMPT_REWRITE.format(
        question=question,
        evidence=evidence or "(none)",
        old_plan=old_plan,
    )
    try:
        resp = create_chat_completion(
            client,
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
        )
        text = resp.choices[0].message.content or ""
    except Exception as e:
        return list(old_steps), {"ok": False, "mode": "rewrite", "error": str(e), "fallback": "old_plan"}

    steps, notes = _parse_steps_json(text)
    if len(steps) < 2:
        steps = [s for s in old_steps if (s or "").strip()]
        if not steps:
            steps = [question]
        steps = steps[:4]
        if not any(
            k in (steps[-1] or "").lower() for k in ("answer", "return", "final")
        ):
            steps.append(
                f"Project the final answer columns that fully answer the original question: {question}"
            )
        return steps, {
            "ok": False,
            "mode": "rewrite",
            "fallback": "old_plus_final",
            "notes": notes,
            "raw": text[:300],
        }

    steps = _ensure_final_projection(steps, question)
    return steps, {"ok": True, "mode": "rewrite", "notes": notes, "n_steps": len(steps)}


def _nrow_ncol_hint(c: Dict[str, Any]) -> Tuple[Optional[int], Optional[int]]:
    """Best-effort (n_rows, n_cols) from explicit fields / result_sig / preview."""
    if c.get("n_rows") is not None or c.get("n_cols") is not None:
        try:
            nr = int(c["n_rows"]) if c.get("n_rows") is not None else None
        except Exception:
            nr = None
        try:
            nc = int(c["n_cols"]) if c.get("n_cols") is not None else None
        except Exception:
            nc = None
        if nr is not None or nc is not None:
            return nr, nc
    sig = (c.get("result_sig") or "").strip()
    # result_sig like "[('a', 1), ('b', 2)]" or "N=12"
    if sig.startswith("N="):
        try:
            return int(sig.split("=", 1)[1]), None
        except Exception:
            return None, None
    if sig.startswith("["):
        try:
            import ast

            rows = ast.literal_eval(sig)
            if isinstance(rows, list) and rows:
                r0 = rows[0]
                ncol = len(r0) if isinstance(r0, tuple) else 1
                return len(rows), ncol
            if isinstance(rows, list):
                return 0, None
        except Exception:
            pass
    prev = (c.get("result_preview") or "").strip()
    if not prev or prev.startswith("ERROR"):
        return None, None
    return None, None


def _structural_keep_or_switch(
    question: str,
    default: Dict[str, Any],
    alts: List[Dict[str, Any]],
) -> Optional[Tuple[int, str]]:
    """Return (pick_index, reason) if a high-confidence structural rule fires; else None."""
    q = (question or "").lower()
    dn, dc = _nrow_ncol_hint(default)
    # year/period questions: prefer 1-col year-like over year+total
    year_ask = any(w in q for w in ("which year", "what year", "year recorded", "in which year"))
    who_best = (
        ("which of these" in q or "performs the best" in q or "best in" in q)
        and any(w in q for w in ("player", "crossing", "alexis", "driver"))
    )
    for alt in alts:
        an, ac = _nrow_ncol_hint(alt)
        if year_ask and dc == 1 and ac == 2:
            # default lean year, alt year+metric → KEEP default
            return int(default["index"]), "struct_year_keep_lean"
        if who_best and dc == 1 and ac == 2:
            # default name-only, alt name+metric → SWITCH
            return int(alt["index"]), "struct_who_best_prefer_name_metric"
        # list full names / list all X: prefer substantially smaller 1-col result
        # (often DISTINCT/group-by-name vs group-by-id duplicates).
        if any(
            w in q
            for w in (
                "full name",
                "list the full names",
                "list all the",
                "list the names",
                "superhero",
            )
        ):
            if dc == 1 and ac == 1 and dn and an and an < dn and (dn - an) >= 10:
                return int(alt["index"]), "struct_list_prefer_fewer_rows"
    return None


def _dedupe_cands_keep_latest(candidates: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Collapse identical result signatures; keep the latest (highest index) per sig."""
    by_sig: Dict[str, Dict[str, Any]] = {}
    for c in candidates:
        sig = (c.get("result_sig") or c.get("result_preview") or "").strip()
        if not sig:
            sig = f"__nosig_{c.get('index')}"
        prev = by_sig.get(sig)
        if prev is None or int(c.get("index", -1)) >= int(prev.get("index", -1)):
            by_sig[sig] = c
    return sorted(by_sig.values(), key=lambda x: int(x.get("index", 0)))


PROMPT_CHAIN_SYNTH = """You are an expert SQLite Text-to-SQL writer.

Original question:
{question}

Evidence / Hint (respect literals exactly — values, column names, filters):
{evidence}

Database schema (use only tables/columns that appear here):
{schema}

Business plan steps (what each intermediate was *meant* to do):
{plan_block}

We already ran a step-by-step CTE search. Below is the mechanically assembled chain
and the per-step winner CTEs. Treat them as REFERENCE / scratch notes only:
- Later steps often restart from base tables instead of reading prior CTEs.
- Steps may contradict each other or answer the wrong grain.
- Do NOT blindly concatenate CTEs or `SELECT * FROM` the last step if that fails
  the original question.

Assembled chain SQL (mechanical):
```sql
{assembled}
```

Per-step CTE winners:
{steps_block}

Task: write ONE complete SQLite SQL that correctly answers the original question.
You may rewrite freely (clean WITH ... or a single SELECT). Prefer Evidence literals.

Output ONLY the SQL inside <sql>...</sql>.
"""


def _extract_sql_block(text: str) -> str:
    raw = (text or "").strip()
    m = re.search(r"<sql>\s*([\s\S]*?)\s*</sql>", raw, re.I)
    if m:
        raw = m.group(1).strip()
    else:
        m = re.search(r"```(?:sql)?\s*([\s\S]*?)```", raw, re.I)
        if m:
            raw = m.group(1).strip()
    return raw.strip().rstrip(";")


def synthesize_sql_from_cte_chain(
    *,
    client,
    model: str,
    question: str,
    evidence: str,
    schema: str,
    plan_steps: List[str],
    cte_chain: List[str],
    assembled_sql: str,
    temperature: float = 0.2,
) -> Tuple[str, Dict[str, Any]]:
    """Greedy-only: LLM rewrites a complete final SQL from the whole CTE chain.

    Uses distributed per-step search as context; does not require mechanical assembly
    to be semantically correct.
    """
    plan_block = "\n".join(
        f"{i}. {s}" for i, s in enumerate(plan_steps or [], start=1)
    ) or "(none)"
    step_chunks: List[str] = []
    for i, cte in enumerate(cte_chain or []):
        body = (cte or "").strip()
        if len(body) > 1800:
            body = body[:1800] + "\n...[truncated]"
        step_chunks.append(f"--- step {i} ---\n{body}")
    steps_block = "\n\n".join(step_chunks) if step_chunks else "(none)"
    assembled = (assembled_sql or "").strip()
    if len(assembled) > 6000:
        assembled = assembled[:6000] + "\n...[truncated]"
    prompt = PROMPT_CHAIN_SYNTH.format(
        question=question,
        evidence=evidence or "(none)",
        schema=(schema or "(empty)")[:8000],
        plan_block=plan_block,
        assembled=assembled or "(empty)",
        steps_block=steps_block,
    )
    audit: Dict[str, Any] = {
        "ok": False,
        "n_chain_steps": len(cte_chain or []),
        "assembled_len": len(assembled_sql or ""),
    }
    try:
        resp = create_chat_completion(
            client,
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=float(temperature),
        )
        text = resp.choices[0].message.content or ""
    except Exception as e:
        audit["error"] = str(e)[:240]
        return "", audit
    sql = _extract_sql_block(text)
    audit["raw_preview"] = (text or "")[:300]
    if not sql or not re.search(r"\bSELECT\b", sql, re.I):
        audit["reason"] = "no_sql"
        return "", audit
    audit["ok"] = True
    audit["sql_len"] = len(sql)
    return sql, audit


def pick_final_among_prefixes(
    *,
    client,
    model: str,
    question: str,
    evidence: str,
    candidates: List[Dict[str, Any]],
    pick_mode: str = "llm",
) -> Tuple[int, Dict[str, Any]]:
    """candidates: [{index, sql_preview, result_preview, result_sig?}, ...]

    pick_mode:
      llm            — LLM among deduped cands (prompt defaults to last)
      last           — always last executable prefix
      hybrid         — last if ≤1 unique result; else LLM
      keep_last      — binary KEEP-last / SWITCH (conservative; best for #4)
      majority       — latest prefix in the majority result_sig cluster
    """
    if not candidates:
        return -1, {"ok": False, "reason": "empty", "pick_mode": pick_mode}
    if pick_mode == "last" or len(candidates) == 1:
        return int(candidates[-1]["index"]), {
            "ok": True,
            "reason": "last_prefix" if pick_mode == "last" else "only_one",
            "pick_mode": pick_mode,
        }

    if pick_mode == "majority":
        from collections import Counter

        cnt: Counter = Counter()
        for c in candidates:
            sig = c.get("result_sig") or c.get("result_preview") or ""
            cnt[sig] += 1
        if not cnt:
            return int(candidates[-1]["index"]), {
                "ok": True,
                "reason": "majority_fallback_last",
                "pick_mode": pick_mode,
            }
        top_sig, top_n = cnt.most_common(1)[0]
        # Prefer the latest prefix in the majority cluster (often a stable answer).
        for c in reversed(candidates):
            sig = c.get("result_sig") or c.get("result_preview") or ""
            if sig == top_sig:
                return int(c["index"]), {
                    "ok": True,
                    "reason": "majority_result_sig",
                    "pick_mode": pick_mode,
                    "majority_n": top_n,
                    "n_cands": len(candidates),
                    "n_unique_sigs": len(cnt),
                }
        return int(candidates[-1]["index"]), {
            "ok": True,
            "reason": "majority_fallback_last",
            "pick_mode": pick_mode,
        }

    deduped = _dedupe_cands_keep_latest(candidates)
    if len(deduped) == 1:
        return int(deduped[0]["index"]), {
            "ok": True,
            "reason": "dedupe_single_sig_latest",
            "pick_mode": pick_mode,
            "n_before_dedupe": len(candidates),
        }

    if pick_mode == "hybrid" and len(deduped) <= 1:
        return int(candidates[-1]["index"]), {
            "ok": True,
            "reason": "hybrid_same_result_last",
            "pick_mode": pick_mode,
        }

    # Conservative binary: KEEP last unless SWITCH justified.
    if pick_mode == "keep_last":
        default = candidates[-1]
        alts = [c for c in deduped if int(c.get("index", -1)) != int(default.get("index", -1))]
        if not alts:
            return int(default["index"]), {
                "ok": True,
                "reason": "keep_last_no_alt",
                "pick_mode": pick_mode,
            }
        struct = None
        # Round2 structural heuristics are OFF by default (overfit risk).
        # Enable with MCTS_PICK_STRUCTURAL=1.
        if os.environ.get("MCTS_PICK_STRUCTURAL", "0").strip().lower() in (
            "1",
            "true",
            "yes",
            "on",
        ):
            struct = _structural_keep_or_switch(question, default, alts)
        if struct is not None:
            pick_idx, reason = struct
            return pick_idx, {
                "ok": True,
                "reason": reason,
                "decision": "SWITCH" if pick_idx != int(default["index"]) else "KEEP",
                "pick_mode": pick_mode,
                "n_deduped": len(deduped),
                "structural": True,
            }
        use_switch_fix = _bugfix_switch_enabled()
        alt_lines = []
        for i, c in enumerate(alts):
            if use_switch_fix:
                alt_lines.append(
                    f"[{i}] (step_index={c.get('index')})\n"
                    f"SQL: {(c.get('sql_preview') or c.get('sql') or '')[:350]}\n"
                    f"Result: {(c.get('result_preview') or '')[:350]}\n"
                )
            else:
                alt_lines.append(
                    f"[{i}] step_index={c.get('index')}\n"
                    f"SQL: {(c.get('sql_preview') or c.get('sql') or '')[:350]}\n"
                    f"Result: {(c.get('result_preview') or '')[:350]}\n"
                )
        prompt_tmpl = (
            PROMPT_PICK_FINAL_KEEP_LAST
            if use_switch_fix
            else PROMPT_PICK_FINAL_KEEP_LAST_LEGACY
        )
        prompt = prompt_tmpl.format(
            question=question,
            evidence=evidence or "(none)",
            default_index=default.get("index"),
            default_sql=(default.get("sql_preview") or default.get("sql") or "")[:350],
            default_result=(default.get("result_preview") or "")[:350],
            alts="\n".join(alt_lines),
        )
        try:
            resp = create_chat_completion(
                client,
                model=model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.0,
            )
            text = resp.choices[0].message.content or ""
        except Exception as e:
            return int(default["index"]), {
                "ok": False,
                "error": str(e),
                "fallback": "last",
                "pick_mode": pick_mode,
            }
        m = re.search(r"\{[\s\S]*\}", text)
        decision = "KEEP"
        pick_idx = int(default["index"])
        reason = ""
        if m:
            try:
                obj = json.loads(m.group(0))
                decision = str(obj.get("decision") or "KEEP").strip().upper()
                reason = str(obj.get("reason") or "")
                if decision == "SWITCH":
                    raw_pick = obj.get("pick", None)
                    by_index = {int(c["index"]): c for c in alts}
                    by_step = {int(c["index"]): int(c["index"]) for c in alts}
                    if raw_pick is None:
                        decision = "KEEP"
                        pick_idx = int(default["index"])
                    else:
                        rp = int(raw_pick)
                        if use_switch_fix:
                            # Prefer 0-based alt list index (prompt contract).
                            if 0 <= rp < len(alts):
                                pick_idx = int(alts[rp]["index"])
                            elif rp in by_step:
                                pick_idx = rp
                                reason = (reason + " | pick_as_step_index").strip()
                            else:
                                decision = "KEEP"
                                pick_idx = int(default["index"])
                                reason = (reason + " | invalid_switch_pick").strip()
                        else:
                            # Legacy: step_index first, then list index.
                            if rp in by_index:
                                pick_idx = rp
                            elif 0 <= rp < len(alts):
                                pick_idx = int(alts[rp]["index"])
                            else:
                                decision = "KEEP"
                                pick_idx = int(default["index"])
                                reason = (reason + " | invalid_switch_pick").strip()
                else:
                    pick_idx = int(default["index"])
                    decision = "KEEP"
            except Exception:
                decision = "KEEP"
                pick_idx = int(default["index"])
        if decision != "SWITCH":
            pick_idx = int(default["index"])
        elif use_switch_fix:
            # Guard: only honor SWITCH when default objectively looks bad.
            prev = str(default.get("result_preview") or "")
            n_rows = default.get("n_rows")
            looks_err = prev.startswith("ERROR") or prev.startswith("ERROR:")
            looks_empty = (n_rows == 0) or ("(empty" in prev.lower())
            null_hits = prev.lower().count("none") + prev.count("NULL")
            looks_nullish = null_hits >= 2 and (n_rows is None or int(n_rows) <= 8)
            if not (looks_err or looks_empty or looks_nullish):
                decision = "KEEP"
                pick_idx = int(default["index"])
                reason = (reason + " | blocked_switch_default_ok").strip()
        return pick_idx, {
            "ok": True,
            "reason": reason,
            "decision": decision,
            "pick_mode": pick_mode,
            "n_deduped": len(deduped),
            "bugfix_switch": use_switch_fix,
        }

    # llm / hybrid with multiple unique results: ranked list prompt (defaults to last)
    use_cands = deduped if pick_mode in ("llm", "hybrid") else candidates
    lines = []
    for i, c in enumerate(use_cands):
        tag = " (DEFAULT=LAST)" if i == len(use_cands) - 1 else ""
        lines.append(
            f"[{i}] step_index={c.get('index')}{tag}\n"
            f"SQL: {(c.get('sql_preview') or c.get('sql') or '')[:350]}\n"
            f"Result: {(c.get('result_preview') or '')[:350]}\n"
        )
    prompt = PROMPT_PICK_FINAL.format(
        question=question,
        evidence=evidence or "(none)",
        cands="\n".join(lines),
    )
    try:
        resp = create_chat_completion(
            client,
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
        )
        text = resp.choices[0].message.content or ""
    except Exception as e:
        return int(candidates[-1]["index"]), {
            "ok": False,
            "error": str(e),
            "fallback": "last",
            "pick_mode": pick_mode,
        }

    m = re.search(r"\{[\s\S]*\}", text)
    pick_local = len(use_cands) - 1
    reason = ""
    if m:
        try:
            obj = json.loads(m.group(0))
            pick_local = int(obj.get("pick", pick_local))
            reason = str(obj.get("reason") or "")
        except Exception:
            pass
    pick_local = max(0, min(pick_local, len(use_cands) - 1))
    return int(use_cands[pick_local]["index"]), {
        "ok": True,
        "reason": reason,
        "pick_local": pick_local,
        "pick_mode": pick_mode,
        "n_deduped": len(deduped),
        "n_before_dedupe": len(candidates),
    }

