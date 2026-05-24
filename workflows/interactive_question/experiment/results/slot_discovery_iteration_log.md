# Slot Discovery Iteration Log (Path B)

## V0 dev baseline

```json
{
  "axis_recall": 0.7363,
  "value_recall_exact": 0.1411,
  "value_recall_semantic": 0.8544,
  "fp_rate": 0.4243,
  "avg_value_count": 2.48,
  "n_cases": 15
}
```

## Iteration 1 (v1)

- Failure mode observed: Failure mode: combined taxonomy labels and high FP from over-generated Column/Row slots. Change: strict calibration, exact subcategory labels, slot count cap.
- Prompt change: ~37 line diffs vs previous version (see prompts/slot_discovery_v1.md)
- Dev metrics: {'axis_recall': 0.6941, 'value_recall_exact': 0.2522, 'value_recall_semantic': 0.7511, 'fp_rate': 0.4219, 'avg_value_count': 1.81, 'n_cases': 15}
- Diagnosis after run: Low axis recall cases: 1166(0.50), 1481(0.50), 1094(0.57); High FP cases: 829(0.62), 959(0.57), 1481(0.57)
- Decision: revert
- Rationale: no acceptable gain (axis 0.694, sem 0.751, fp 0.422) vs current 0.736/0.854/0.424

## Iteration 2 (v2)

- Failure mode observed: Failure mode: residual FP from speculative Column/Row Structure slots. Change: precision-first omit-when-uncertain, require schema table.column in fragments, tighter slot cap.
- Prompt change: ~22 line diffs vs previous version (see prompts/slot_discovery_v2.md)
- Dev metrics: {'axis_recall': 0.6875, 'value_recall_exact': 0.2222, 'value_recall_semantic': 0.6867, 'fp_rate': 0.314, 'avg_value_count': 1.32, 'n_cases': 15}
- Diagnosis after run: Low axis recall cases: 1166(0.40), 1481(0.50), 1094(0.57); High FP cases: 959(0.57), 1481(0.50), 1166(0.43)
- Decision: revert
- Rationale: no acceptable gain (axis 0.688, sem 0.687, fp 0.314) vs current 0.736/0.854/0.424

## Iteration 3 (v3)

- Failure mode observed: Failure mode: axis recall still below target on dev while FP elevated. Change: balance precision-first with explicit coverage checklist for Table/Join/Projection/Formula/Boundary/Ranking.
- Prompt change: ~16 line diffs vs previous version (see prompts/slot_discovery_v3.md)
- Dev metrics: {'axis_recall': 0.7075, 'value_recall_exact': 0.2044, 'value_recall_semantic': 0.7733, 'fp_rate': 0.2886, 'avg_value_count': 1.56, 'n_cases': 15}
- Diagnosis after run: Low axis recall cases: 1166(0.50), 1481(0.50), 1094(0.57); High FP cases: 959(0.50), 1481(0.50), 480(0.40)
- Decision: revert
- Rationale: no acceptable gain (axis 0.708, sem 0.773, fp 0.289) vs current 0.736/0.854/0.424

