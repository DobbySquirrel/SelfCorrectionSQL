# AutoClarify v0 — 100q root cause (T6/T7/T8)

- Generated: 2026-06-04T15:48:53.403224+00:00

## T6. Pool recall on triggered subsets

| subset | n | gold_in_any_cluster | gold_in_top3_cluster |
|---|---:|---:|---:|
| abstain (M/R/O) | 17 | 0 (0%) | 0 (0%) |
| non-abstain (M/R/O hard) | 11 | 0 (0%) | 0 (0%) |
| Reference/Value soft | 33 | 0 (0%) | 0 (0%) |

Pool-level (`gold_in all_sql_variants`, stricter than cluster match):

| subset | n | gold_in_pool |
|---|---:|---:|
| abstain (M/R/O) | 17 | 0 (0%) |
| non-abstain (M/R/O hard) | 11 | 0 (0%) |
| Reference/Value soft | 33 | 0 (0%) |

## T7. Non-abstain 11 — pool vs choice vs constraint

| qid | type | gold_rank | chosen_rank | pool_after_prune | gold_in_pool | gold_satisfies |
|---:|---|---:|---:|---:|---|---:|
| 31 | out_of_pool | - | 1 | 0/15 | False | False |
| 50 | out_of_pool | - | 2 | 0/10 | False | False |
| 347 | out_of_pool | - | 1 | 0/13 | False | False |
| 547 | out_of_pool | - | 1 | 19/19 | False | False |
| 557 | out_of_pool | - | 2 | 8/8 | False | False |
| 765 | out_of_pool | - | 2 | 8/8 | False | False |
| 915 | out_of_pool | - | 2 | 0/9 | False | False |
| 1037 | out_of_pool | - | 1 | 0/17 | False | False |
| 1238 | out_of_pool | - | 1 | 0/15 | False | False |
| 1275 | out_of_pool | - | 1 | 0/8 | False | False |
| 1531 | out_of_pool | - | 1 | 11/11 | False | False |

### T7 type counts

| type | n | meaning |
|---|---:|---|
| out_of_pool | 11 | gold not in rollout pool |
| wrong_choice | 0 | gold in pool, Answer picked wrong cluster |
| good_choice_bad_constraint | 0 | right cluster, constraint/sql_satisfies mismatch |
| in_pool_no_cluster | 0 | gold in pool but cluster bucketing miss |
| constraint_ok | 0 | gold satisfies compiled constraint |

## T8. Empty-prune 7 — does rep_sql satisfy its own constraint?

If `rep_satisfies=False`, constraint_hint or sql_satisfies is broken (not a regenerate issue).

| qid | axis | choice | rep_satisfies | gold_satisfies | violated | constraint |
|---:|---|---|---|---|---|---|
| 31 | Ranking | A | False | False | ['required_order', 'required_limit'] | `level=hard; axis=Ranking; order=DESC; limit=1` |
| 50 | Ranking | B | False | False | ['required_limit'] | `level=hard; axis=Ranking; order=DESC; limit=7` |
| 347 | Output | A | False | False | ['required_select_columns'] | `level=hard; axis=Output; select=['c.id', 'r.text']` |
| 915 | Ranking | B | False | False | ['required_limit'] | `level=hard; axis=Ranking; order=ASC; limit=0` |
| 1037 | Measure | A | False | False | ['required_agg'] | `level=hard; axis=Measure; agg=('SUM', 'preferred_foot')` |
| 1238 | Output | A | False | False | ['required_select_columns'] | `level=hard; axis=Output; select=['l.hgb', 'p.birthday', 'p.id', 'p.sex']` |
| 1275 | Measure | A | False | False | ['required_agg'] | `level=hard; axis=Measure; agg=('COUNT', 'p.id')` |

### T8 detail (rep SQL head)

- **qid=31** rank=1: `WITH ranked_schools AS (     SELECT          `Percent (%) Eligible Free (K-12)` AS eligible_free_rate,         ROW_NUMBE`
- **qid=50** rank=2: `WITH seventh_highest_math_school AS (     SELECT s.School, s.Street     FROM schools s     INNER JOIN satscores ss ON s.`
- **qid=347** rank=1: `WITH final_answer AS (     SELECT          c.id,         r.text AS ruling_text,         CASE              WHEN c.hasCont`
- **qid=915** rank=2: `WITH oldest_driver AS (     SELECT nationality     FROM drivers     ORDER BY dob ASC     LIMIT 1 ) SELECT nationality FR`
- **qid=1037** rank=1: `WITH final_result AS (     SELECT          CAST(left_foot_players AS REAL) * 100 / total_players AS percentage     FROM `
- **qid=1238** rank=1: `WITH oldest_sle_patient AS (     SELECT p.ID, p.SEX     FROM Patient p     INNER JOIN Laboratory l ON p.ID = l.ID     WH`
- **qid=1275** rank=1: `WITH cte1 AS (     SELECT l.ID, l.CENTROMEA, l.SSB, p.SEX     FROM Laboratory l     INNER JOIN Patient p ON l.ID = p.ID `

## Decision tree verdict

- **Case A (T7):** all 11 non-abstain are `out_of_pool` — gold never in `all_sql_variants`. Not wrong_choice; AnswerAgent cluster pick is moot when gold absent.
- **Case C (T8):** all 7 empty-prune qids have `rep_satisfies=False` — chosen cluster's representative SQL does **not** satisfy its own compiled constraint. This is constraint_hint / sql_satisfies bug; **regenerate cannot fix**.
- **Case B:** 0 wrong_choice on non-abstain 11.

### Combined read (A + C, not either/or)

- T6: triggered 61/61 subsets show **0% gold_in_pool** (abstain 17, non-abstain 11, Ref/Val 33).
- Saved=0 on non-abstain is **primarily recall** (Case A), not Answer picking wrong cluster.
- But hard prune empty-pool failures are **primarily constraint self-check fail** (Case C).
- **Priority:** fix Case C (constraint/sql_satisfies) so prune is not self-contradictory; then invest in regenerate for Case A recall.
- Do **not** tune Answer abstain prompt for saved — no gold in pool to save.
