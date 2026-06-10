# E1 plan top-k vs E0

Generated: 2026-06-09T11:05:04.156167+00:00

## Overall

| | Recall | Hit@1 R3 | Hit@8 |
|---|---:|---:|---:|
| E0 B′ | 13/30 | 0/30 | 13/30 |
| E1 plan | 15/30 | 6/30 | 15/30 |
| Δ | +2 | +6 | +2 |

Plan dedup mean: 0.00 dist: {0: 30}

## By bucket (Δ Hit@1 / Δ Recall)

- **A_search_miss_recoverable**: Δrecall=+4, Δhit@1=+1 (10q)
- **B_selection_miss**: Δrecall=+0, Δhit@1=+5 (10q)
- **C_both_no_recall**: Δrecall=+0, Δhit@1=+0 (5q)
- **D_s7_false_consensus**: Δrecall=-2, Δhit@1=+0 (5q)
