# AutoClarify v0 — log_only 100q replay

- Generated: 2026-06-04T16:01:32.130184+00:00
- Source: `/hpc2hdd/home/sshen190/wtao565/SelfCorrectionSQL/workflows/mcts_v4/test/out/clarify_a0_a2_coder/v4_calib_498q_coder_rollouts8.json`
- Qids: `workflows/mcts_v4/test/out/clarify_a0_a2_coder/analysis/s8_100q_qids.txt`
- Mode: log_only | mock_llm=False

## Summary metrics

| Metric | Value |
|---|---:|
| triggered / 100 | 61 (61.0%) |
| ClarifyAgent parse success (triggered) | 11/61 (18.0%) |
| AnswerAgent abstain rate (triggered) | 11.5% |
| mean confidence (non-abstain) | 0.850 |
| evidence non-empty (answers) | 11/11 (100.0%) |

## Per-axis distribution

| Axis | Count |
|---|---:|
| Measure | 4 |
| Ranking | 2 |
| Output | 2 |
| Value | 2 |
| Reference | 1 |

- Top axis share: 36.4% (watch if >70%)

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
| ClarifyAgent parse | ≥95% | 18.0% |
| abstain rate | 30–70% | 11.5% |
| top axis share | ≤70% | 36.4% |
| evidence non-empty | high | 100.0% |
| trigger rate (~56% calib) | ~56% | 61.0% |
| R2_hit hurt | 0 | 0 |

## v3 Case-C metrics

| metric | value |
|---|---:|
| self_check_failed | 0 |
| hard prune would apply (level=hard, self-check pass) | 3 |
| empty pool (simulated hard, pool_after=0) | 0 |
| non-abstain M/R/O | 3 |

## v2 vs v3 comparison (v2 = pre self-check baseline)

| metric | v2 | v3 |
|---|---:|---:|
| triggered | 61 | 61 |
| non-abstain (M/R/O) | 11 | 3 |
| self_check_failed | n/a | 0 |
| hard prune would apply | 11 | 3 |
| empty pool fallbacks | 7 | 0 |
| saved | 0 | 0 |
| hurt | 0 | 0 |
| R2_hit hurt | 0 | 0 |

Trace: `workflows/mcts_v4/test/out/clarify_a0_a2_coder/analysis/clarify_v0_log_only_100q_v3.trace.jsonl`
