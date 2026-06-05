# Silent Failure Global Audit (P0)

Generated: 2026-06-03T13:53:09

Read-only scan. Definition: `total_s < 5 && rollout_count > 0 && sql == ""`.

ef2 known set: 51 qids from `qids_ef2_51.json`

## 1. Summary table

| dataset | n | silent_failure | % | non_ef2_sf | soft_anomaly_rows |
|---|---:|---:|---:|---:|---:|
| final_498 | 498 | 51 | 10.2% | 0 | 0 |
| baseline_498 | 498 | 0 | 0.0% | 0 | 0 |
| a0_30 | 30 | 0 | 0.0% | 0 | 0 |
| a3_30 | 30 | 0 | 0.0% | 0 | 0 |
| a0_30_r20 | 30 | 0 | 0.0% | 0 | 0 |

## 2. Per-dataset detail

### final_498 (`workflows/mcts_v4/test/out/clarify_a0_a2_coder/v4_final_498q_coder_rollouts8.json`)

- Silent failures: **51** (ef2: 51, non-ef2: 0)

| qid | db_id | total_s | rollout_count | sql_len |
|-----|-------|--------:|--------------:|--------:|
| 1025 | european_football_2 | 1.43 | 8 | 0 |
| 1028 | european_football_2 | 1.486 | 8 | 0 |
| 1029 | european_football_2 | 1.57 | 8 | 0 |
| 1030 | european_football_2 | 1.469 | 8 | 0 |
| 1031 | european_football_2 | 1.36 | 8 | 0 |
| 1032 | european_football_2 | 1.361 | 8 | 0 |
| 1035 | european_football_2 | 1.414 | 8 | 0 |
| 1036 | european_football_2 | 1.44 | 8 | 0 |
| 1037 | european_football_2 | 1.451 | 8 | 0 |
| 1039 | european_football_2 | 1.361 | 8 | 0 |
| 1040 | european_football_2 | 1.438 | 8 | 0 |
| 1042 | european_football_2 | 1.338 | 8 | 0 |
| 1044 | european_football_2 | 1.449 | 8 | 0 |
| 1048 | european_football_2 | 1.439 | 8 | 0 |
| 1057 | european_football_2 | 1.359 | 8 | 0 |
| 1058 | european_football_2 | 1.388 | 8 | 0 |
| 1068 | european_football_2 | 1.466 | 8 | 0 |
| 1076 | european_football_2 | 1.455 | 8 | 0 |
| 1078 | european_football_2 | 1.422 | 8 | 0 |
| 1079 | european_football_2 | 1.372 | 8 | 0 |
| 1080 | european_football_2 | 1.545 | 8 | 0 |
| 1084 | european_football_2 | 1.413 | 8 | 0 |
| 1088 | european_football_2 | 1.448 | 8 | 0 |
| 1091 | european_football_2 | 1.36 | 8 | 0 |
| 1092 | european_football_2 | 1.393 | 8 | 0 |
| 1094 | european_football_2 | 1.345 | 8 | 0 |
| 1096 | european_football_2 | 1.422 | 8 | 0 |
| 1098 | european_football_2 | 1.431 | 8 | 0 |
| 1102 | european_football_2 | 1.444 | 8 | 0 |
| 1103 | european_football_2 | 1.374 | 8 | 0 |
| 1105 | european_football_2 | 1.359 | 8 | 0 |
| 1107 | european_football_2 | 1.323 | 8 | 0 |
| 1110 | european_football_2 | 1.43 | 8 | 0 |
| 1113 | european_football_2 | 1.403 | 8 | 0 |
| 1114 | european_football_2 | 1.374 | 8 | 0 |
| 1115 | european_football_2 | 1.468 | 8 | 0 |
| 1116 | european_football_2 | 1.332 | 8 | 0 |
| 1122 | european_football_2 | 1.412 | 8 | 0 |
| 1124 | european_football_2 | 1.445 | 8 | 0 |
| 1130 | european_football_2 | 1.41 | 8 | 0 |
| 1133 | european_football_2 | 1.476 | 8 | 0 |
| 1134 | european_football_2 | 1.42 | 8 | 0 |
| 1135 | european_football_2 | 1.289 | 8 | 0 |
| 1136 | european_football_2 | 1.346 | 8 | 0 |
| 1139 | european_football_2 | 1.474 | 8 | 0 |
| 1141 | european_football_2 | 1.37 | 8 | 0 |
| 1144 | european_football_2 | 1.29 | 8 | 0 |
| 1145 | european_football_2 | 1.372 | 8 | 0 |
| 1146 | european_football_2 | 1.485 | 8 | 0 |
| 1147 | european_football_2 | 1.473 | 8 | 0 |
| 1148 | european_football_2 | 1.458 | 8 | 0 |

**By db_id (silent):**
- `european_football_2`: 51

### baseline_498 (`workflows/mcts_v4/test/out/v4_arcwise_full_result_rollouts_20.json`)

- No silent failures.

### a0_30 (`workflows/mcts_v4/test/out/clarify_a0_a2_coder/v4_a0_30q_coder_rollouts8.json`)

- No silent failures.

### a3_30 (`workflows/mcts_v4/test/out/clarify_a0_a2_coder/v4_a3_30q_coder_rollouts8.json`)

- No silent failures.

### a0_30_r20 (`workflows/mcts_v4/test/out/clarify_a0_a2_coder/v4_a0_30q_coder_rollouts20.json`)

- No silent failures.

## 3. ef2 confirmation (final_498)

- ef2 qids present in final_498: **51/51**
- ef2 qids hitting silent failure: **51/51**
- **51/51 全部命中** — 与 T2 基线一致（known infra issue）

## 4. 🚨 红线判定

**Verdict: `CLEAN`** — 除 ef2 外 silent failure = 0 → 数据池干净，paper 主线安全

- Total non-ef2 silent failures (all 5 datasets): **0**


---

**🛑 Hard checkpoint**: P0 complete — await user review before P1/P2.

