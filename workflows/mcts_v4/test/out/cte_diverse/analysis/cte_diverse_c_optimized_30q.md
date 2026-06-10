# Diverse-C optimized — 30q sanity (2-call + skip M_verify)

Generated: 2026-06-08T16:14:21

Optimized JSON: `workflows/mcts_v4/test/out/cte_diverse/v4_diverse_c_opt_30q_coder_rollouts8.json`

## Three-way comparison

| metric | calib | diverse-C 3call+Mverify | **2call+noMverify** |
|---|---:|---:|---:|
| recall | 26/30 | 28/30 | **26/30** |
| Hit@1 (R2) | 21/30 | 21/30 | **23/30** |
| mean time/qid (s) | 111.2s | 310.4s | **179.7s** |
| mean cte_gen_s | 92.9s | 0.0s | **144.1s** |
| mean sql_gen_s | 12.9s | 33.0s | **25.0s** |
| mean db_exec_s | 4.7s | 10.2s | **6.5s** |
| mean CTE/expand (trace) | — | 10.4 | **7.7** |
| fallback rate | — | 0.0% | **0.0%** |
| m_verify_skipped (trace) | — | 0.0% | **100.0%** |

## Gates (optimized config)

- recall >= 26: PASS (26/30)
- Hit@1 >= 20: PASS (23/30)
- time/qid <= 150s (≤1.4× calib ~111s): FAIL (179.7s)

**Overall: FAIL**

## Cost model check

Per expand (theoretical LLM-equiv): calib ~11, diverse 3call ~23, **optimized ~9** (2 diverse + 0 M_verify).

- Optimized / calib wall time ratio: **1.62×**
- Optimized mean cte_gen_s: **144.1s/qid** (includes diverse LLM; calib was un-metered in old runs)
