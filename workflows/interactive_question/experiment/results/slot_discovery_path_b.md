# Stage 1 Slot Discovery — Path B Report

## Section 1: Split protocol & lock-down

- Dev (15): `23, 36, 85, 145, 219, 347, 366, 480, 587, 829, 959, 1094, 1166, 1338, 1481`
- Test (15): `32, 46, 125, 197, 263, 349, 391, 483, 639, 894, 1031, 1149, 1255, 1387, 1482`
- Split file: `experiment/results/slot_discovery_split.json`
- Locked prompt: `prompts/slot_discovery_v1.md` (source: **v0**; tag: `slot-discovery-v1-locked`)

## Section 2: V0 baseline on dev/test

| split | axis_recall | value_sem | value_exact | fp_rate | avg_value_count |
|---|---|---|---|---|---|
| dev | 0.7363 | 0.8544 | 0.1411 | 0.4243 | 2.48 |
| test | 0.7983 | 0.6067 | 0.0767 | 0.4083 | 2.57 |

## Section 3: Iteration log

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


## Section 4: Locked prompt results (v0 vs best)

| split | version | axis_recall | value_sem | value_exact | fp_rate | avg_value_count |
|---|---|---|---|---|---|---|
| dev | v0 | 0.7363 | 0.8544 | 0.1411 | 0.4243 | 2.48 |
| dev | v0 | 0.7363 | 0.8544 | 0.1411 | 0.4243 | 2.48 |
| test | v0 | 0.7983 | 0.6067 | 0.0767 | 0.4083 | 2.57 |
| test | v0 | 0.7983 | 0.6067 | 0.0767 | 0.4083 | 2.57 |

### Dev-test gap (Step 4)

| metric | dev v0 | dev best | test locked |
|---|---|---|---|
| axis recall | 0.7363 | 0.7363 | 0.7983 |
| value recall (sem) | 0.8544 | 0.8544 | 0.6067 |
| value recall (exact) | 0.1411 | 0.1411 | 0.0767 |
| FP rate | 0.4243 | 0.4243 | 0.4083 |
| avg value count | 2.48 | 2.48 | 2.57 |

### Test per-case (15)

| qid | gold_axis | pred_axis | axis_recall | value_sem | fp_rate |
|---|---|---|---|---|---|
| 32 | 7 | 9 | 0.857 | 0.8333 | 0.333 |
| 46 | 7 | 9 | 0.857 | 1.0 | 0.333 |
| 125 | 5 | 4 | 0.400 | 1.0 | 0.500 |
| 197 | 7 | 8 | 0.857 | 0.1667 | 0.250 |
| 263 | 6 | 7 | 0.833 | 0.4 | 0.286 |
| 349 | 7 | 9 | 0.714 | 0.2 | 0.444 |
| 391 | 6 | 9 | 0.833 | 0.8 | 0.444 |
| 483 | 6 | 7 | 0.833 | 0.6 | 0.286 |
| 639 | 5 | 7 | 0.800 | 0.5 | 0.429 |
| 894 | 6 | 8 | 0.833 | 0.6 | 0.375 |
| 1031 | 6 | 8 | 0.667 | 0.75 | 0.500 |
| 1149 | 2 | 9 | 1.000 | 0.5 | 0.778 |
| 1255 | 9 | 9 | 0.889 | 0.25 | 0.111 |
| 1387 | 5 | 9 | 0.800 | 0.75 | 0.556 |
| 1482 | 5 | 8 | 0.800 | 0.75 | 0.500 |

## Section 5: Test traces

### qid=1149

**NL:** Are there more in-patient or outpatient who were male? What is the deviation in percentage?...

**Gold axes:**
- `aggregate:SELECT`: `case when in_count > out_count then 'in-patient' else 'outpatient' end`
- `source:FROM`: `aggregate_state_query_1`

**Predicted slots (mapped):**
- `aggregate:DISTINCT`: 2 candidates
- `aggregate:GROUP`: 4 candidates
- `aggregate:LIMIT`: 2 candidates
- `aggregate:ORDERBY`: 2 candidates
- `aggregate:SELECT`: 7 candidates
- `combine:COMBINE_WHERE`: 2 candidates
- `combine:JOINS`: 2 candidates
- `filter:WHERE`: 6 candidates
- `source:FROM`: 2 candidates

**Commentary:** axis_recall=1.00, fp=0.78, value_sem=0.5.

### qid=1255

**NL:** For the patients with an abnormal Ig M level, what is the most common disease they are diagnosed with?...

**Gold axes:**
- `aggregate:DISTINCT`: `true`
- `aggregate:GROUP`: `diagnosis`
- `aggregate:LIMIT`: `1`
- `aggregate:ORDERBY`: `count(*) desc`
- `aggregate:SELECT`: `patient.id, patient.diagnosis`
- `combine:BASE`: `source_state_query_2`
- `combine:JOINS`: `{"on": "source_state_query_2.id = laboratory.id", "target": "laborator`
- `filter:WHERE`: `laboratory.igm <= 40 or laboratory.igm >= 400`
- `source:FROM`: `patient`

**Predicted slots (mapped):**
- `aggregate:DISTINCT`: 2 candidates
- `aggregate:GROUP`: 4 candidates
- `aggregate:LIMIT`: 2 candidates
- `aggregate:ORDERBY`: 5 candidates
- `aggregate:SELECT`: 5 candidates
- `combine:COMBINE_WHERE`: 4 candidates
- `combine:JOINS`: 2 candidates
- `filter:WHERE`: 10 candidates
- `source:FROM`: 2 candidates

**Commentary:** axis_recall=0.89, fp=0.11, value_sem=0.25.

### qid=639

**NL:** Based on posts posted by Community, calculate the percentage of posts that use the R language....

**Gold axes:**
- `aggregate:SELECT`: `cast(sum(case when tags.tagname = 'r' then 1 else 0 end) as float) * 1`
- `combine:BASE`: `source_state_query_1`
- `combine:JOINS`: `{"on": "tags.excerptpostid = posthistory.postid", "target": "tags", "t`
- `filter:WHERE`: `users.displayname = 'community'`
- `source:FROM`: `users`

**Predicted slots (mapped):**
- `aggregate:DISTINCT`: 2 candidates
- `aggregate:GROUP`: 7 candidates
- `aggregate:SELECT`: 12 candidates
- `combine:COMBINE_WHERE`: 4 candidates
- `combine:JOINS`: 5 candidates
- `filter:WHERE`: 13 candidates
- `source:FROM`: 3 candidates

**Commentary:** axis_recall=0.80, fp=0.43, value_sem=0.5.

### qid=125

**NL:** For loans contracts which are still running where client are in debt, state their district percentage unemployment rate increment from year 1995 to 1996....

**Gold axes:**
- `aggregate:SELECT`: `(district.a13 - district.a12) / district.a12 * 100`
- `combine:BASE`: `source_state_query_1`
- `combine:COMBINE_WHERE`: `loan.status = 'd' and not district.a12 is null and not district.a13 is`
- `combine:JOINS`: `{"on": "source_state_query_1.account_id = account.account_id", "target`
- `source:FROM`: `loan`

**Predicted slots (mapped):**
- `aggregate:GROUP`: 2 candidates
- `aggregate:SELECT`: 6 candidates
- `combine:JOINS`: 2 candidates
- `filter:WHERE`: 4 candidates

**Commentary:** axis_recall=0.40, fp=0.50, value_sem=1.0.

## Section 6: 判断

**Verdict (no_improvement):** No improvement vs v0 on test (<3pp); accept v0 baseline for paper.

Dev-test axis recall gap: -6.2 pp
