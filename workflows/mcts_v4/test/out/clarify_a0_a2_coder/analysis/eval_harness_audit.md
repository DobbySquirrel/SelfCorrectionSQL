# T11 — Evaluation Harness Audit

- Generated: 2026-06-04T16:53:18.082331+00:00
- Trace: `clarify_v0_log_only_100q_v4.trace.jsonl` | 100q qids | triggered=61

## T11.1 v4 saved/hurt under three judges (hard-sim subset)

Saved = exec-equiv gold survives hard prune AND R2 miss under same judge.

| judge | saved | hurt | R2_hit hurt |
|---|---:|---:|---:|
| J1 normalize_sql | 0 | 0 | — |
| J2 compare_with_gold (exec-equiv) | 0 | 0 | 0 |
| J3 AST extract + sql_satisfies | 0 | 0 | — |

**Prior replay (J1 only):** saved=0, hurt=0.

## T11.2 Triggered qids with calib exec-equiv gold (21/61)

R2 Hit@1 under J2 on these 21: **17/21**

| qid | pool J1 | pool J2 | R2 J1 | R2 J2 | R2 J3 | pick (head) |
|---:|---|---|---|---|---|---|
| 32 | False | True | False | True | False | `WITH top5_schools AS (     SELECT          CASE              WHEN f.`Enrollment ` |
| 213 | False | True | False | True | False | `WITH final_bond_type AS (     SELECT DISTINCT b.bond_type     FROM bond b     JO` |
| 346 | False | True | False | False | False | `WITH final_result AS (     SELECT c.id, c.artist     FROM cards c     INNER JOIN` |
| 371 | False | True | False | False | False | `WITH french_story_spotlight AS (     SELECT COUNT(DISTINCT cs.id) AS french_coun` |
| 547 | False | True | False | True | False | `WITH cte1 AS (     SELECT u.Id, u.Age     FROM users u     WHERE u.Age > 65 ) SE` |
| 563 | False | True | False | True | False | `WITH user_comment AS (     SELECT PostId     FROM comments     WHERE UserId = 30` |
| 685 | False | True | False | False | False | `WITH final_answer AS (     SELECT          p.ViewCount AS total_views,         u` |
| 765 | False | True | False | True | False | `WITH stealth_power_id AS (     SELECT id     FROM superpower     WHERE power_nam` |
| 822 | False | True | False | True | False | `WITH green_skinned_villains AS (     SELECT s.id, s.superhero_name, c.colour AS ` |
| 875 | False | True | False | True | False | `WITH race_year AS (     SELECT year     FROM races     WHERE raceId = 901 ) SELE` |
| 959 | False | True | False | True | False | `WITH cte1 AS (     SELECT r.raceId, r.year, r.name, res.driverId, res.fastestLap` |
| 1042 | False | True | False | True | False | `WITH final_leagues AS (     SELECT l.name     FROM Match m     JOIN League l ON ` |
| 1229 | False | True | False | True | False | `WITH patients_with_high_tg AS (     SELECT DISTINCT p.ID, p.Birthday     FROM Pa` |
| 1238 | False | True | False | True | False | `WITH oldest_sle_patient AS (     SELECT p.ID, p.SEX     FROM Patient p     INNER` |
| 1275 | False | True | False | True | False | `WITH cte1 AS (     SELECT l.ID, l.CENTROMEA, l.SSB, p.SEX     FROM Laboratory l ` |
| 1317 | False | True | False | True | False | `WITH students_with_medium_tshirt AS (     SELECT COUNT(*) AS answer     FROM att` |
| 1338 | False | True | False | True | False | `WITH all_expenses_approved AS (     SELECT          CASE              WHEN COUNT` |
| 1401 | False | True | False | True | False | `WITH event_info AS (     SELECT event_id, event_name     FROM event     WHERE ev` |
| 1422 | False | True | False | True | True | `SELECT DISTINCT type FROM event WHERE location = 'MU 215';` |
| 1486 | False | True | False | False | False | `WITH sme_currency_counts AS (     SELECT          SUM(CASE WHEN Currency = 'CZK'` |
| 1505 | False | True | False | True | False | `WITH cte1 AS (     SELECT c.CustomerID, c.Currency, y.Consumption     FROM custo` |

- pool has exec-equiv gold but not normalize-string: **21** qids
- R2 hits J2 but not J1: **17** qids

## T11.3 Canonical Hit@1 recommendation

| check | expected | J1 normalize | J2 exec-equiv | J3 AST |
|---|---:|---:|---:|---:|
| calib_498 R2 replay | **370** (audit) | 6 | **347** | 43 |
| merged_ef2 R2 replay | **364** (D2b) | 6 | **350** | 48 |
| calib stored gold_match | 370 | 370 | — | — |

### Verdict

**Canonical Hit@1 judge = `exec_equiv` (`compare_with_gold`)** — locked in `eval_pipeline_audit.md` / D2b.

| baseline source | Hit@1 | pick fn | judge |
|---|---:|---|---|
| D2b merged_ef2 | **364/498** | `selector_replay.pick_r2` | compare_with_gold |
| calib stored `gold_match` | **370/498** | stored sql (≡ pick_r2 at write) | compare_with_gold |
| T11 replay (this script) | 347/498 calib, 350/498 merged | `SQLSelector.select` | compare_with_gold |

⚠️ **23-qid gap** (370 vs 347) = `SQLSelector.select` ≠ D2b `pick_r2` tiebreak on subset of qids. Clarify replay should align pick fn with D2b in follow-up; **judge choice is still exec-equiv**.

- J1 normalize on R2 pick: **6/498** — not usable for Hit@1 (stored gold_match=370 proves this).
- J3 AST partial constraints: **43/498** — constraint-debug only, not Hit@1.
- Replay patched: default `--judge exec_equiv`; see `clarify_v0_log_only_100q_v4_canonical.md`.

## T11.4 Decision tree (triggered 21 exec-equiv pool)

| signal | value |
|---|---|
| R2 J2 hit on 21 pool-gold qids | **17/21** |
| v4 saved under J2 | **0** |
| Action | **skip_r1b_fix_evaluator** |

- R2 already hits exec-equiv gold on most pool-gold qids → **R1b not primary**; fix evaluator + clarify saved narrative first.

## Harness map (pre-fix)

| component | judge before T11 |
|---|---|
| sql_satisfies / hard prune | AST partial constraints |
| replay saved/hurt | normalize_sql |
| D2b / project Hit@1 | compare_with_gold |
| integration trace gold_match | normalize_sql |

→ **Three mismatched layers**; canonical = exec-equiv for Hit@1 only.
