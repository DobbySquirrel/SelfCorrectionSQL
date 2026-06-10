# Diverse-C t09+N3 — 30q (3-call 0.3/0.6/0.9, N=3, skip M_verify)

Generated: 2026-06-08T16:57:17

JSON: `workflows/mcts_v4/test/out/cte_diverse/v4_diverse_c_opt_t09_n3_30q_coder_rollouts8.json`

## Four-way comparison

| metric | calib | div3 3call+Mverify | opt 2call+N5 | **t09 3call+N3** |
|---|---:|---:|---:|---:|
| recall | 26/30 | 28/30 | 26/30 | **26/30** |
| Hit@1 (R2) | 21/30 | 21/30 | 23/30 | **21/30** |
| mean time/qid (s) | 111.2s | 310.4s | 179.7s | **150.5s** |
| mean cte_gen_s | 92.9s | 0.0s | 144.1s | **123.7s** |
| mean sql_gen_s | 12.9s | 33.0s | 25.0s | **23.1s** |
| mean CTE/expand | — | 10.4 | 7.7 | **6.2** |
| mean LLM calls/expand | — | 3 | 2 | **3** |
| m_verify_skipped | — | 0.0% | 100.0% | **100.0%** |

## vs prior opt (2call+N5)

- recall Δ: **+0** (26 vs 26)
- Hit@1 Δ: **-2** (21 vs 23)
- time/qid Δ: **-29.1s** (150.5 vs 179.7)
