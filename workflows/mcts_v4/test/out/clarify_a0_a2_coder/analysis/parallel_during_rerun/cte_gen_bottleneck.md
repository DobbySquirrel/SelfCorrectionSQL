# CTE Gen Bottleneck (O3)

Generated: 2026-06-03T14:10:25

Scope: final_498 excl. ef2 (**447** qids).


## 1. Overall time split (Σ over questions)

| Phase | Σ seconds | % of Σ total |
|---|---:|---:|
| cte_gen_s | 42532 | 83.2% |
| sql_gen_s | 5698 | 11.1% |
| db_exec_s | 2643 | 5.2% |
| other | 262 | 0.5% |

Per-question mean cte share: **84.5%** (±8.3 pp std)

## 2. Long tail (total_s)

| P50 | P75 | P95 | max |
|---:|---:|---:|---:|
| 101.3s | 161.2s | 270.8s | 1094.5s |

### Top-20 by cte_gen_s

| qid | db | cte_s | total_s | share | ddl_chars | depth |
|-----|-----|------:|--------:|------:|----------:|------:|
| 402 | card_games | 355.3 | 372.7 | 95% | 24,419 | 8 |
| 92 | financial | 310.4 | 350.1 | 89% | 6,960 | 8 |
| 32 | california_schools | 288.1 | 316.1 | 91% | 19,270 | 8 |
| 24 | california_schools | 283.2 | 346.5 | 82% | 19,238 | 8 |
| 1387 | student_club | 276.5 | 328.8 | 84% | 10,518 | 8 |
| 125 | financial | 275.2 | 301.2 | 91% | 6,960 | 8 |
| 1481 | debit_card_specializing | 273.0 | 348.5 | 78% | 1,613 | 8 |
| 149 | financial | 261.0 | 278.7 | 94% | 6,947 | 8 |
| 1524 | debit_card_specializing | 258.2 | 270.8 | 95% | 1,620 | 8 |
| 36 | california_schools | 255.5 | 295.6 | 86% | 19,265 | 8 |
| 972 | formula_1 | 250.5 | 586.5 | 43% | 13,334 | 8 |
| 866 | formula_1 | 248.6 | 315.1 | 79% | 13,341 | 8 |
| 218 | toxicology | 244.8 | 273.7 | 89% | 2,539 | 8 |
| 31 | california_schools | 244.0 | 274.3 | 89% | 19,237 | 8 |
| 940 | formula_1 | 242.5 | 268.4 | 90% | 13,278 | 8 |
| 232 | toxicology | 242.0 | 267.5 | 90% | 2,524 | 8 |
| 637 | codebase_community | 240.9 | 308.7 | 78% | 11,596 | 8 |
| 944 | formula_1 | 240.8 | 375.9 | 64% | 13,213 | 8 |
| 116 | financial | 238.7 | 274.8 | 87% | 6,960 | 8 |
| 281 | toxicology | 238.0 | 253.9 | 94% | 2,539 | 8 |

## 3. DDL size vs cte_gen_s

- Pearson r (ddl_chars, cte_gen_s): **0.057**

## 4. r scaling (30q a0 r=8 vs r=20)

| Metric | Value |
|---|---|
| Mean cte_gen ratio (r20/r8) | **1.91** |
| Mean wall ratio (r20/r8) | **1.85** |
| Qids where r20 faster | 8 |

**Conclusion**: cte_gen ratio < 2.0 at r=8→20 → partial overlap/amortization exists; profile which layer before leaf-parallel.

## 5. Per-call latency (estimated)

| P50 | P95 | max |
|---:|---:|---:|
| 0.77s | 1.30s | 1.89s |

Estimate: `cte_gen_s / (Σ expansion_steps × ~4 temp groups)` per question.

## 6. Safe perf fixes (ranked)

| Rank | Fix | Expected gain | Risk |
|---:|---|---|---|
| 1 | HTTP keep-alive / shared OpenAI client pool in `cte_generator.py` | 5–15% cte wall | Low |
| 2 | vLLM prefix cache: reuse system+DDL per rollout batch | 10–25% on large DDL | Low if prompt identical |
| 3 | DDL soft compression (drop value stats tail) under token budget | 10–20% on top-20 DDL q | Med — verify no S4 regress |
| 4 | System prompt / schema string interning per question | 3–8% | Low |

**C-class (record only)**: leaf parallelization / multi-depth batch — changes search semantics.

### Rough combined impact (top-3 safe)

If cte is **83%** of wall, 20–30% cte reduction ⇒ **17–25pp** total wall-time drop (~2.8–4.3h on full 498 run).

