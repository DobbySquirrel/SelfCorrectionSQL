# AutoClarify v0 — 100q diagnosis (saved=0)

- Generated: 2026-06-04T15:42:18.598673+00:00
- Trace: `clarify_v0_log_only_100q.trace.jsonl`
- Calib: `v4_calib_498q_coder_rollouts8.json`

## Interpretation

Gold is **not** passed to LLM. This report decomposes why simulated hard `saved=0`.

## T1. Trigger funnel

| stage | n | note |
|---|---:|---|
| triggered | 61 | of 100 qids |
| → Reference/Value (soft forced) | 33 | v0 never simulates hard |
| → Measure/Ranking/Output (hard-eligible) | 28 | |
| &nbsp;&nbsp; abstain | 17 | AnswerAgent |
| &nbsp;&nbsp; non-abstain | 11 | |
| &nbsp;&nbsp; non-abstain → hard level | 11 | conf≥0.80, M/R/O |
| &nbsp;&nbsp; hard prune ≠ empty | 4 | |
| &nbsp;&nbsp; gold satisfies constraint | 0 | actual answer path |
| &nbsp;&nbsp; gold survives prune | 0 | |
| &nbsp;&nbsp; saved (R2 picks gold, baseline missed) | 0 | **= 0** |

## T2. Abstain root cause (hard-eligible abstain only, n=17)

| reason | n |
|---|---:|
| mandatory_multiple_support | 15 |
| mandatory_no_verbatim_quote | 2 |

## T3. Counter-factual upper bound (oracle = gold-cluster candidate)

For each **hard-eligible abstain** qid: pick candidate matching the cluster that contains gold SQL.

| metric | n |
|---|---:|
| abstain qids with gold in top cluster | 0 |
| abstain qids gold NOT in any top cluster | 17 |
| oracle choice → gold satisfies constraint | 0 |
| of those → saved (R2 picks gold after prune) | 0 |
| of those → R2_hit hurt (gold pruned) | 0 |

**Ceiling read:**
- Oracle saved ≤ 2 → **Framework/constraint/sql_satisfies** likely caps upside; prompt alone won't fix.

## T4. Non-abstain breakdown (hard-eligible, hard level)

| qid | axis | choice | gold_cluster? | gold_satisfies? | prune_n | R2_hit? | saved? |
|---:|---|---|---|---|---|---:|---:|---:|
| 31 | Ranking | A | False | False | 0/15 | False | False |
| 50 | Ranking | B | False | False | 0/10 | False | False |
| 347 | Output | A | False | False | 0/13 | False | False |
| 547 | Measure | A | False | False | 19/19 | False | False |
| 557 | Measure | B | False | False | 8/8 | False | False |
| 765 | Measure | B | False | False | 8/8 | False | False |
| 915 | Ranking | B | False | False | 0/9 | False | False |
| 1037 | Measure | A | False | False | 0/17 | False | False |
| 1238 | Output | A | False | False | 0/15 | False | False |
| 1275 | Measure | A | False | False | 0/8 | False | False |
| 1531 | Measure | A | False | False | 11/11 | False | False |

## T5. Per-bucket trigger/save matrix

| bucket | total | triggered | non-abstain | hard-eligible | saved |
|---|---:|---:|---:|---:|---:|
| missed_by_all | 30 | 19 | 8 | 9 | 0 |
| S7_subset | 16 | 12 | 2 | 6 | 0 |
| calib_only | 9 | 6 | 1 | 4 | 0 |
| final_only | 18 | 9 | 3 | 4 | 0 |
| R2_hit_random | 27 | 15 | 6 | 5 | 0 |

## Key failure modes (non-abstain hard path)

- gold **violates** compiled constraint: 11 qids → 31, 50, 347, 547, 557, 765, 915, 1037, 1238, 1275, 1531
- hard prune **empties** pool: 7 qids → 31, 50, 347, 915, 1037, 1238, 1275

## Next-step decision (do NOT change prompt yet without team review)

1. **Primary blocker:** constraint/sql_satisfies or gold-not-in-pool — not AnswerAgent abstain rate.
2. Investigate T4 empty-prune + gold_satisfies=false qids before any prompt change.
