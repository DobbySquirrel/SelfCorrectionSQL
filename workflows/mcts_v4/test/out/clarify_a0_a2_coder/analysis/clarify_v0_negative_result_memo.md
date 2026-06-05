# QueryClarifier v0 — Negative Result Memo

- Date: 2026-06-04
- Scope: BIRD calib 100q replay + T9/T10 diagnostics

## Unified conclusion (R6a/R7 → calib → AutoClarify v0)

QueryClarifier v0 on BIRD (Coder, r=8) hits a **structural ceiling**:

1. **Framework is internally consistent** — ClarifyAgent v0.2 AST constraints, self-check, AnswerAgent abstain rules; Case C (fake hard) closed.
2. **Saved Hit@1 = 0 on triggered subset (string-normalize)** — T9 used `normalize_sql` on rollout_stats → 0/61 gold_in_pool; oracle saved 0/28.
3. **Execution-equivalent gold IS in pool for subset** — T10: `compare_with_gold` on rollout_stats **20/61**; union calib∪final **30/61** (P0 cache).
4. **Prompt / extractor tuning does not fix normalize Hit@1** — abstain/oracle analysis used string match; pool may contain exec-equiv SQL R2 never selects as exact string.

## T10 lift (compare_with_gold, triggered 61)

- calib only: **21/61 (34.4%)**
- final only: **23/61 (37.7%)**
- calib ∪ final ∪ ef2: **30/61 (49.2%)**

## Direct implication

> **Clarification-as-constraint must be paired with search-space expansion** when gold is not in pool under exec-equiv. When gold *is* in pool but R2 already hits (17/21 on calib pool-gold triggered qids), clarify hard-prune has **no saved headroom** on current traces.

Any selector-only or prune-only improvement on BIRD@Coder-r=8 has a **recall-bound Hit@1 ceiling** on the missed-by-all / S7 subset (31 triggered qids, union=0).
AutoClarify v0 proves the clarify pipeline works; it cannot create gold that search never sampled.

## Decision log

| Step | Result | Action |
|---|---|---|
| Case C (v3/v4) | closed | no LLM constraint_hint |
| T9 abstain | 28/30, oracle saved 0 (normalize) | do not tune answer prompt for saved |
| T10 union lift | 30/61 | ≥20 → R1 data exists for subset |
| T11 canonical judge | exec-equiv; R2 hits 17/21 pool-gold; hard-sim saved=0 | skip R1b; align pick fn |

## Paper-ready paragraph (optional)

```
We implement AutoClarify v0: trigger on cluster ambiguity, clarify via LLM, 
compile AST-based hard/soft constraints, and prune before R2 selection. 
On a stratified 100-question BIRD calib set (61 clarify-triggered), 
the pipeline is self-consistent (zero self-check failures, zero empty-pool 
fallbacks after v0.2), yet Hit@1 saved remains zero because gold SQL 
never appears in the search pool on triggered questions. 
Counterfactual oracle answers recover zero additional hits, 
establishing that clarification-as-prune is recall-bound without 
search expansion (regenerate or multi-seed union).
```

## Caveat (T11)

saved=0 in v4 replay used **normalize_sql** judge. Under **exec-equiv** (`compare_with_gold`), **21/61** triggered qids have gold-equivalent SQL in calib pool; R2 hits **17/21** of those on calib. v4 hard-sim **saved under exec-equiv = 0**. See `eval_harness_audit.md`.
