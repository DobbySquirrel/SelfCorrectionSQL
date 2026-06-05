# S8 — 20q Smoke Subset

Generated: 2026-06-04T17:28:53

## Rule

Per bucket: **first N** qids in `s8_100q_buckets.json` order (no re-sample).

## Bucket counts

| Bucket | 100q | smoke |
|---|---:|---:|
| calib_only | 9 | 2 |
| final_only | 18 | 4 |
| missed_by_all | 30 | 6 |
| S7_subset | 16 | 3 |
| R2_hit_random | 27 | 5 |
| **Total** | 100 | **20** |

## SHA256 (sorted qid list)

```
3060436925243cd9510aebaefd08ccce522440c65e055b17188e96fb73dc5949
```

File: `s8_20q_smoke_qids.txt`

## smoke ⊂ full

✅ All **20** smoke qids ∈ `s8_100q_qids.txt`

## Per-bucket qids (smoke order)

### calib_only (2)

`32, 346`

### final_only (4)

`26, 31, 72, 186`

### missed_by_all (6)

`25, 36, 50, 145, 173, 197`

### S7_subset (3)

`37, 48, 169`

### R2_hit_random (5)

`128, 136, 213, 231, 371`

🛑 Do not run real_llm until replay wiring confirmed.
