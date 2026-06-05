# S8 — Stratified 100q Sample (AutoClarify real_llm)

Generated: 2026-06-04T17:28:53
Seed: **20240601**

## Bucket counts

| Bucket | Rule | n |
|---|---|---:|
| calib_only | P0 exclusive, **all** | 9 |
| final_only | P0 exclusive, **all** | 18 |
| missed_by_all | P0 pool n=66, `random.sample(k=30)` | 30 |
| S7_subset | s7_41 minus prior buckets, `k=16` (available=16) | 16 |
| R2_hit_random | merged R2 hit minus prior, `k=27` | 27 |
| **Total** | | **100** |

## SHA256 (qid list, sorted)

```
70629e68eebe3aedb99297f39a5f77c9c2f534b8e94d22b3badb069820a05ce2
```

File: `workflows/mcts_v4/test/out/clarify_a0_a2_coder/analysis/s8_100q_qids.txt`

## Per-bucket qids

### calib_only (9)

`32, 346, 465, 685, 955, 1238, 1486, 1490, 1505`

### final_only (18)

`26, 31, 72, 186, 219, 234, 530, 637, 694, 726, 788, 868, 915, 1166, 1254, 1270, 1387, 1472`

### missed_by_all (30)

`25, 36, 50, 145, 173, 197, 212, 263, 347, 349, 383, 412, 557, 587, 671, 794, 861, 894, 901, 1011, 1037, 1169, 1227, 1243, 1252, 1357, 1359, 1376, 1529, 1531`

### S7_subset (16)

`37, 48, 169, 201, 533, 640, 772, 904, 948, 1002, 1080, 1136, 1256, 1302, 1389, 1498`

### R2_hit_random (27)

`128, 136, 213, 231, 371, 547, 563, 578, 707, 710, 717, 765, 822, 875, 959, 1042, 1078, 1198, 1229, 1275, 1312, 1317, 1338, 1401, 1422, 1426, 1514`

## Overlap confirmation

✅ **Zero overlap** between buckets (pairwise intersection empty).

## Inputs

- `workflows/mcts_v4/test/out/clarify_a0_a2_coder/analysis/p0_union_recall.json` — exclusive buckets
- `workflows/mcts_v4/test/out/clarify_a0_a2_coder/s7_41_qids.txt` — 41 S7 qids
- `workflows/mcts_v4/test/out/clarify_a0_a2_coder/analysis/d2b_g4_498_replay.json` — merged R2 replay `rows[].r2_hit` (n=364)

🛑 **Do not** run real_llm or MCTS until smoke plan is approved.
