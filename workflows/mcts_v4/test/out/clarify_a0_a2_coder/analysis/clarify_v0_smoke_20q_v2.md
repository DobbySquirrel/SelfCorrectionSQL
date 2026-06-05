# AutoClarify v0 — smoke 20q replay

- Generated: 2026-06-04T15:29:27.412036+00:00
- Source: `workflows/mcts_v4/test/out/clarify_a0_a2_coder/v4_calib_498q_coder_rollouts8.json`
- Qids: `workflows/mcts_v4/test/out/clarify_a0_a2_coder/analysis/s8_20q_smoke_qids.txt`
- Mode: log_only | mock_llm=False

## Summary metrics

| Metric | Value |
|---|---:|
| triggered / 20 | 13 (65.0%) |
| ClarifyAgent parse success (triggered) | 13/13 (100.0%) |
| AnswerAgent abstain rate (triggered) | 69.2% |
| mean confidence (non-abstain) | 0.850 |
| evidence non-empty (answers) | 13/13 (100.0%) |

## Per-axis distribution

| Axis | Count |
|---|---:|
| Reference | 8 |
| Measure | 3 |
| Output | 1 |
| Ranking | 1 |

- Top axis share: 61.5% (watch if >70%)

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

## R2 baseline paired diff (log_only: should be empty)

- changed qids: 0

## Smoke / gate checklist

| Check | Target | Actual |
|---|---|---:|
| ClarifyAgent parse | ≥95% | 100.0% |
| abstain rate | 30–70% | 69.2% |
| top axis share | ≤70% | 61.5% |
| evidence non-empty | high | 100.0% |
| trigger rate (~56% calib) | ~56% | 65.0% |
| R2_hit hurt | 0 | 0 |

Trace: `workflows/mcts_v4/test/out/clarify_a0_a2_coder/analysis/clarify_v0_smoke_20q_v2.trace.jsonl`
