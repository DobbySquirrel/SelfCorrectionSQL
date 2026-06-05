# Calibrated hurt spike — qid 1506

Generated: 2026-06-04T00:40:32

Stage 1 hurt vs **a3 R2** (v2 hash, same config as calibrated).

- a3 `optimal_sql` / R2 replay: `WITH distinct_descriptions AS (
    SELECT DISTINCT p.Description
    FROM trans…`
- calib `optimal_sql`: `…`

### a3 R2 baseline (frozen rollout_stats)

| sig (12) | total_count | total_visit | max_rollout_r | variants |
|---|---:|---:|---:|---:|
| `91ff4fdf6fbc…` | 120 | 16 | 1.0000 | 120 |

**Per-rollout**

| rid | reward | leaf_visit | #buckets | bucket counts | R2 pick sig |
|---:|---:|---:|---:|---|---|
| 1 | 1.0000 | 1 | 1 | top=15/8 legacy_r=1.8750 cal_r=1.8750 |
| 2 | 1.0000 | 1 | 1 | top=15/8 legacy_r=1.8750 cal_r=1.8750 |
| 3 | 1.0000 | 2 | 1 | top=15/8 legacy_r=1.8750 cal_r=1.8750 |
| 4 | 1.0000 | 1 | 1 | top=15/8 legacy_r=1.8750 cal_r=1.8750 |
| 5 | 1.0000 | 3 | 1 | top=15/8 legacy_r=1.8750 cal_r=1.8750 |
| 6 | 1.0000 | 2 | 1 | top=15/8 legacy_r=1.8750 cal_r=1.8750 |
| 7 | 1.0000 | 4 | 1 | top=15/8 legacy_r=1.8750 cal_r=1.8750 |
| 8 | 1.0000 | 2 | 1 | top=15/8 legacy_r=1.8750 cal_r=1.8750 |

- R2 SQL (first 120 chars): `WITH distinct_descriptions AS (
    SELECT DISTINCT p.Description
    FROM transactions_1k t
    JOIN gasstations g ON t…`
- R0 SQL (first 120 chars): `WITH distinct_descriptions AS (
    SELECT DISTINCT p.Description
    FROM transactions_1k t
    JOIN gasstations g ON t…`

### calibrated run (new search + rewards)

| sig (12) | total_count | total_visit | max_rollout_r | variants |
|---|---:|---:|---:|---:|
| `e4c4e6b16298…` | 60 | 10 | 1.0000 | 60 |
| `91ff4fdf6fbc…` | 60 | 5 | 1.0000 | 60 |

**Per-rollout**

| rid | reward | leaf_visit | #buckets | bucket counts | R2 pick sig |
|---:|---:|---:|---:|---|---|
| 1 | 1.0000 | 1 | 1 | top=15/8 legacy_r=1.8750 cal_r=1.8750 |
| 2 | 1.0000 | 1 | 1 | top=15/8 legacy_r=1.8750 cal_r=1.8750 |
| 3 | 1.0000 | 1 | 1 | top=15/8 legacy_r=1.8750 cal_r=1.8750 |
| 4 | 1.0000 | 2 | 1 | top=15/8 legacy_r=1.8750 cal_r=1.8750 |
| 5 | 1.0000 | 1 | 1 | top=15/8 legacy_r=1.8750 cal_r=1.8750 |
| 6 | 1.0000 | 3 | 1 | top=15/8 legacy_r=1.8750 cal_r=1.8750 |
| 7 | 1.0000 | 2 | 1 | top=15/8 legacy_r=1.8750 cal_r=1.8750 |
| 8 | 1.0000 | 4 | 1 | top=15/8 legacy_r=1.8750 cal_r=1.8750 |

- R2 SQL (first 120 chars): `WITH cte1 AS (
    SELECT g.GasStationID, g.Country
    FROM gasstations g
    WHERE g.Country = 'CZE'
    LIMIT 10
)
SE…`
- R0 SQL (first 120 chars): `WITH cte1 AS (
    SELECT g.GasStationID, g.Country
    FROM gasstations g
    WHERE g.Country = 'CZE'
    LIMIT 10
)
SE…`

## Interpretation (5 min)

- Mixed pattern — inspect per-rollout table above.

Does not block Stage 2 (S7 recall-lost pool).