# Regression Breakdown: 84 questions (Task 5)

Definition: baseline Hit@1 ✓ → Final Hit@1 ✗. Also fixed **31** questions (inverse).

**Net Hit@1 change**: +31 − 84 = **-53**

## Recall post-mortem

| Category | Count | % of 84 regressions |
|---|---:|---:|
| Recall also lost (search failure) | 49 | 58.3% |
| Recall retained (selection failure only) | 35 | 41.7% |

> **Conclusion**: Of 84 regressions, **58%** lost recall (true search regression, confounded with model/r/hash), **42%** still had a correct path in Final (selection-only — clarification target).

⚠ Final vs baseline differs in model (Coder vs Qwen), rollouts (8 vs 20), and hash (v2 vs legacy).

## 1. db_id bucket

| db_id | regressions | total q | regress rate |
|---|---:|---:|---:|
| european_football_2 | 41 | 51 | 80.4% |
| card_games | 8 | 52 | 15.4% |
| toxicology | 7 | 40 | 17.5% |
| thrombosis_prediction | 6 | 50 | 12.0% |
| superhero | 5 | 52 | 9.6% |
| debit_card_specializing | 4 | 30 | 13.3% |
| formula_1 | 4 | 66 | 6.1% |
| codebase_community | 3 | 49 | 6.1% |
| student_club | 2 | 48 | 4.2% |
| california_schools | 2 | 30 | 6.7% |
| financial | 2 | 30 | 6.7% |

## 2. Gold SQL complexity

| bucket | count |
|---|---:|
| simple | 60 |
| medium | 18 |
| complex | 6 |

## 3. Shard (498 4-shard run)

| shard | regressions | shard total | rate |
|---|---:|---:|---:|
| w0 | 12 | 125 | 9.6%
| w1 | 46 | 125 | 36.8% ⚠
| w2 | 10 | 125 | 8.0%
| w3 | 16 | 123 | 13.0%

**⚠ Shard imbalance**: shard(s) [1] exceed 1.5× mean regression rate — check vLLM port/model consistency.

## 4. Cluster count (max result_buckets per rollout, Final)

- mean: 0.76
- median: 1
- ≥2 clusters: 17/84

## 5. Multi-bucket expansion steps (Final, v2 sig)

- mean steps with ≥2 buckets: 7.44

## 6. Wall-clock (Final, regressions)

- mean total_s: 80.4s (vs 498 overall mean 102.8s)

## Distribution summary

- Regressions span **11** databases; top-3: european_football_2(41), card_games(8), toxicology(7).
- Complexity: simple=60, medium=18, complex=6.

