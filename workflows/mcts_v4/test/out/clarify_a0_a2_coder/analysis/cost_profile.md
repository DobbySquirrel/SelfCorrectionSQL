# Cost Profile (Task 2)

Source: `stats.timing` aggregated per question. **No per-rollout LLM/DB counters in JSON.**

| Config | n | r | mean total_s | mean LLM_s (cte+sql) | mean DB_s | LLM % | mean rollout_count |
|---|---:|---:|---:|---:|---:|---:|---:|
| 30q A0 r=8 legacy | 30 | 8 | 88.4 | 84.1 | 4.05 | 95% | 8 |
| 30q A3 r=8 v2 | 30 | 8 | 103.4 | 96.7 | 6.23 | 94% | 8 |
| 30q A0 r=20 legacy | 30 | 20 | 170.2 | 162.7 | 7.10 | 96% | 20 |
| 498q Final r=8 v2 | 498 | 8 | 102.8 | 97.0 | 5.31 | 94% | 8 |
| 498q Baseline r=20 | 498 | 20 | 107.8 | 68.3 | 1.55 | 63% | 20 |

## Narrative

> Question-level wall-clock at r=8 (30q Coder): **88s/q** (LLM ≈ 95% via cte_gen+sql_gen). DB exec negligible (~4.05s/q).
> r=8 → r=20 on same 30q: time **88s → 170s** (1.92×), Hit@1 **+1/30** (+3.3pp), Recall unchanged (25/30).
> 498q Final (r=8 v2): **103s/q** mean; extrapolated full pass ≈ 14.2h single-worker equivalent.

## Limitations

- No r=2 or 300-q runs → **cost-quality curve for r=2 unavailable**.
- No LLM token counts / call counts in JSON → cannot report tokens/rollout.
- 498 baseline vs Final differ in **model + rollouts + hash** → cost compare is indicative only.

