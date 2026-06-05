# AutoClarify v0 — log_only 100q replay

- Generated: 2026-06-04T15:38:30.449402+00:00
- Source: `workflows/mcts_v4/test/out/clarify_a0_a2_coder/v4_calib_498q_coder_rollouts8.json`
- Qids: `workflows/mcts_v4/test/out/clarify_a0_a2_coder/analysis/s8_100q_qids.txt`
- Mode: log_only | mock_llm=False

## Summary metrics

| Metric | Value |
|---|---:|
| triggered / 100 | 61 (61.0%) |
| ClarifyAgent parse success (triggered) | 61/61 (100.0%) |
| AnswerAgent abstain rate (triggered) | 67.2% |
| mean confidence (non-abstain) | 0.850 |
| evidence non-empty (answers) | 61/61 (100.0%) |

## Per-axis distribution

| Axis | Count |
|---|---:|
| Reference | 31 |
| Measure | 17 |
| Output | 7 |
| Ranking | 4 |
| Value | 2 |

- Top axis share: 50.8% (watch if >70%)

## Simulated hard enforcement

| Metric | Value |
|---|---:|
| saved (gold would enter final, R2 missed) | 0 |
| hurt (gold hard-pruned) | 0 |
| **R2_hit hurt** (hard prune removed R2-correct SQL) | 0 |
| safety fallback | 0 |
| saved - hurt | 0 |

### Per-axis saved / hurt

| Axis | saved | hurt |
|---|---:|---:|
| Measure | 0 | 0 |
| Output | 0 | 0 |
| Ranking | 0 | 0 |
| Reference | 0 | 0 |
| Value | 0 | 0 |

## R2 baseline paired diff (log_only: should be empty)

- changed qids: 0

## Smoke / gate checklist

| Check | Target | Actual |
|---|---|---:|
| ClarifyAgent parse | ≥95% | 100.0% |
| abstain rate | 30–70% | 67.2% |
| top axis share | ≤70% | 50.8% |
| evidence non-empty | high | 100.0% |
| trigger rate (~56% calib) | ~56% | 61.0% |
| R2_hit hurt | 0 | 0 |

Trace: `workflows/mcts_v4/test/out/clarify_a0_a2_coder/analysis/clarify_v0_log_only_100q.trace.jsonl`
