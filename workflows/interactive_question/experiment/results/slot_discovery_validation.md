# Stage 1 Slot Discovery Validation (BIRD-116)

Validation subset: **30 cases** (E4 Step-B 25 + top open-world by |W|/axis-miss).

## Q1–Q4 判断

| Q | 判断 | Evidence |
|---|---|---|
| Q1 axis coverage | **partial** | avg axis recall = 0.7673; 100% recall = 1/30; <50% = 1/30 |
| Q2 value recall | **yes** | avg semantic recall (hit axes) = 0.7306; exact = 0.1089; micro miss = 0.3008 |
| Q3 false-positive | **partial** | avg FP rate = 0.4163; FP axes = 97/230 |
| Q4 value list size | **yes** | mean=2.53, median=2, min=2, max=5 |

## Per-case 表

| qid | gold_axis_count | predicted_axis_count | axis_recall | value_recall (exact) | value_recall (semantic) | false_positive_rate | avg_value_count |
|---|---|---|---|---|---|---|---|
| 23 | 5 | 6 | 0.600 | 0.3333 | 1.0 | 0.500 | 2.29 |
| 32 | 7 | 9 | 0.857 | 0.3333 | 0.8333 | 0.333 | 2.91 |
| 36 | 7 | 8 | 0.857 | 0.1667 | 0.8333 | 0.250 | 2.5 |
| 46 | 7 | 9 | 0.857 | 0.1667 | 1.0 | 0.333 | 3.0 |
| 85 | 5 | 6 | 0.800 | 0.0 | 1.0 | 0.333 | 2.4 |
| 125 | 5 | 4 | 0.400 | 0.0 | 1.0 | 0.500 | 2.0 |
| 197 | 7 | 8 | 0.857 | 0.0 | 0.1667 | 0.250 | 2.11 |
| 219 | 5 | 4 | 0.600 | 0.0 | 1.0 | 0.250 | 2.0 |
| 263 | 6 | 7 | 0.833 | 0.0 | 0.4 | 0.286 | 2.25 |
| 347 | 5 | 9 | 0.800 | 0.0 | 1.0 | 0.556 | 3.0 |
| 349 | 7 | 9 | 0.714 | 0.0 | 0.2 | 0.444 | 2.71 |
| 366 | 5 | 9 | 0.800 | 0.25 | 0.5 | 0.556 | 3.0 |
| 480 | 5 | 7 | 0.800 | 0.25 | 1.0 | 0.429 | 2.38 |
| 483 | 6 | 7 | 0.833 | 0.4 | 0.6 | 0.286 | 2.14 |
| 587 | 6 | 7 | 0.833 | 0.2 | 1.0 | 0.286 | 2.0 |
| 639 | 5 | 7 | 0.800 | 0.0 | 0.5 | 0.429 | 3.75 |
| 829 | 4 | 9 | 0.750 | 0.3333 | 1.0 | 0.667 | 2.22 |
| 1031 | 6 | 8 | 0.667 | 0.0 | 0.75 | 0.500 | 3.0 |
| 1094 | 7 | 6 | 0.571 | 0.0 | 0.75 | 0.333 | 2.86 |
| 1149 | 2 | 9 | 1.000 | 0.0 | 0.5 | 0.778 | 2.11 |
| 1166 | 10 | 9 | 0.600 | 0.0 | 0.8333 | 0.333 | 2.62 |
| 1255 | 9 | 9 | 0.889 | 0.0 | 0.25 | 0.111 | 2.67 |
| 1387 | 5 | 9 | 0.800 | 0.0 | 0.75 | 0.556 | 3.12 |
| 1481 | 6 | 9 | 0.833 | 0.0 | 0.4 | 0.444 | 3.0 |
| 1482 | 5 | 8 | 0.800 | 0.25 | 0.75 | 0.500 | 2.0 |
| 1338 | 5 | 7 | 0.800 | 0.25 | 1.0 | 0.429 | 2.38 |
| 959 | 5 | 6 | 0.600 | 0.3333 | 1.0 | 0.500 | 2.17 |
| 391 | 6 | 9 | 0.833 | 0.0 | 0.8 | 0.444 | 2.44 |
| 145 | 5 | 8 | 0.800 | 0.0 | 0.5 | 0.500 | 2.5 |
| 894 | 6 | 8 | 0.833 | 0.0 | 0.6 | 0.375 | 2.11 |

## 5 case trace

### qid=1149

**NL:** Are there more in-patient or outpatient who were male? What is the deviation in percentage?

**Gold atomic axes:**

- `aggregate:SELECT`: `case when in_count > out_count then 'in-patient' else 'outpatient' ...`
- `source:FROM`: `aggregate_state_query_1`

**LLM slots → mapped atomic candidates:**

- LLM `Reference Grounding: Table ambiguity` → ['source:FROM']
  - `Patient.Admission`
  - `Examination.Thrombosis`
- LLM `Reference Grounding: Column ambiguity` → ['aggregate:GROUP', 'aggregate:SELECT', 'filter:WHERE']
  - `Patient.Admission ('+' for in-patient, '-' for outpatient)`
  - `Examination.Thrombosis (numeric values indicating severity or type)`
- LLM `Reference Grounding: Join Path ambiguity` → ['combine:JOINS']
  - `Patient.ID = Examination.ID`
  - `Patient.ID = Laboratory.ID`
- LLM `Value Grounding: Value Encoding ambiguity` → ['filter:WHERE']
  - `'+' means in-patient, '-' means outpatient`
  - `'+' means admitted, '-' means not admitted, '' means unknown`
- LLM `Measure Construction: Formula ambiguity` → ['aggregate:SELECT']
  - `(count(male in-patients) - count(male outpatients)) / count(male outpa`
  - `(count(male in-patients) - count(male outpatients)) / count(male in-pa`
- LLM `Measure Construction: Boundary ambiguity` → ['combine:COMBINE_WHERE', 'filter:WHERE']
  - `Include patients with Admission = ''`
  - `Exclude patients with Admission = ''`
- LLM `Ranking Target: Extremum ambiguity` → ['aggregate:LIMIT', 'aggregate:ORDERBY']
  - `Compare absolute counts of male in-patients and male outpatients`
  - `Compare relative percentages of male in-patients and male outpatients`
- LLM `Output Control: Projection ambiguity` → ['aggregate:SELECT']
  - `Output raw counts of male in-patients and male outpatients`
  - `Output percentage deviation only`
  - `Output both raw counts and percentage deviation`
- LLM `Output Control: Row Structure ambiguity` → ['aggregate:DISTINCT', 'aggregate:GROUP']
  - `Group results by gender (e.g., male vs. female)`
  - `Aggregate results for males only`

**Hit/Miss/FP:**

- HIT `aggregate:SELECT`: tier=semantic
- HIT `source:FROM`: tier=miss
- FP atomic axis: `aggregate:DISTINCT`
- FP atomic axis: `aggregate:GROUP`
- FP atomic axis: `aggregate:LIMIT`
- FP atomic axis: `aggregate:ORDERBY`
- FP atomic axis: `combine:COMBINE_WHERE`
- FP atomic axis: `combine:JOINS`
- FP atomic axis: `filter:WHERE`

**一句话:** axis_recall=1.00, value_semantic=0.5, FP_rate=0.78.

### qid=1255

**NL:** For the patients with an abnormal Ig M level, what is the most common disease they are diagnosed with?

**Gold atomic axes:**

- `aggregate:DISTINCT`: `true`
- `aggregate:GROUP`: `diagnosis`
- `aggregate:LIMIT`: `1`
- `aggregate:ORDERBY`: `count(*) desc`
- `aggregate:SELECT`: `patient.id, patient.diagnosis`
- `combine:BASE`: `source_state_query_2`
- `combine:JOINS`: `{"on": "source_state_query_2.id = laboratory.id", "target": "labora...`
- `filter:WHERE`: `laboratory.igm <= 40 or laboratory.igm >= 400`
- `source:FROM`: `patient`

**LLM slots → mapped atomic candidates:**

- LLM `Reference Grounding: Table` → ['source:FROM']
  - `Examination(`aCL IgM`)`
  - `Laboratory(`IGM`)`
- LLM `Reference Grounding: Column` → ['aggregate:SELECT', 'filter:WHERE', 'aggregate:GROUP']
  - `Examination(`aCL IgM`)`
  - `Laboratory(`IGM`)`
- LLM `Reference Grounding: Join Path` → ['combine:JOINS']
  - `Patient -> Examination`
  - `Patient -> Laboratory`
- LLM `Value Grounding: Value Encoding` → ['filter:WHERE']
  - ``aCL IgM` > 0.7`
  - ``IGM` > 56`
  - ``aCL IgM` > 1.0`
  - ``IGM` > 100`
- LLM `Measure Construction: Boundary` → ['filter:WHERE', 'combine:COMBINE_WHERE']
  - ``aCL IgM` >= 0.7`
  - ``aCL IgM` > 0.7`
  - ``IGM` >= 56`
  - ``IGM` > 56`
- LLM `Ranking Target: Extremum` → ['aggregate:ORDERBY', 'aggregate:LIMIT']
  - `COUNT(Diagnosis)`
  - `MODE(Diagnosis)`
- LLM `Ranking Target: Method` → ['aggregate:ORDERBY']
  - `Return all tied diseases`
  - `Return the first disease alphabetically`
  - `Return the disease with the highest total Ig M level`
- LLM `Output Control: Projection` → ['aggregate:SELECT']
  - `Only the most common disease`
  - `Most common disease and its frequency`
  - `Most common disease and associated patient count`
- LLM `Output Control: Row Structure` → ['aggregate:DISTINCT', 'aggregate:GROUP']
  - `One row with one disease`
  - `Multiple rows for each tied disease`

**Hit/Miss/FP:**

- HIT `aggregate:DISTINCT`: tier=miss
- HIT `aggregate:GROUP`: tier=miss
- HIT `aggregate:LIMIT`: tier=miss
- HIT `aggregate:ORDERBY`: tier=miss
- HIT `aggregate:SELECT`: tier=miss
- MISS axis (not predicted): `combine:BASE`
- HIT `combine:JOINS`: tier=semantic
- HIT `filter:WHERE`: tier=semantic
- HIT `source:FROM`: tier=miss
- FP atomic axis: `combine:COMBINE_WHERE`

**一句话:** axis_recall=0.89, value_semantic=0.25, FP_rate=0.11.

### qid=829

**NL:** Which publisher created more superheroes: DC or Marvel Comics? Find the difference in the number of superheroes.

**Gold atomic axes:**

- `aggregate:SELECT`: `sum(case when publisher."publisher_name" = 'marvel comics' then 1 e...`
- `combine:BASE`: `source_state_query_2`
- `combine:JOINS`: `{"on": "source_state_query_2.\"publisher_id\" = publisher.\"id\"", ...`
- `source:FROM`: `superhero`

**LLM slots → mapped atomic candidates:**

- LLM `Reference Grounding: Table` → ['source:FROM']
  - `superhero`
  - `publisher`
- LLM `Reference Grounding: Column` → ['aggregate:SELECT', 'filter:WHERE', 'aggregate:GROUP']
  - `superhero.publisher_id`
  - `publisher.publisher_name`
- LLM `Reference Grounding: Join Path` → ['combine:JOINS']
  - `superhero.publisher_id = publisher.id`
  - `superhero.publisher_id = publisher.publisher_name`
- LLM `Value Grounding: Value Encoding` → ['filter:WHERE']
  - `'DC'`
  - `'Marvel Comics'`
  - `'DC Comics'`
  - `'Marvel'`
- LLM `Measure Construction: Formula` → ['aggregate:SELECT']
  - `COUNT(superhero.id) WHERE publisher_name = 'DC' - COUNT(superhero.id) `
  - `ABS(COUNT(superhero.id) WHERE publisher_name = 'DC' - COUNT(superhero.`
- LLM `Measure Construction: Boundary` → ['filter:WHERE', 'combine:COMBINE_WHERE']
  - `WHERE publisher_id IS NOT NULL`
  - `WHERE publisher_id IS NULL OR publisher_id IS NOT NULL`
- LLM `Ranking Target: Extremum` → ['aggregate:ORDERBY', 'aggregate:LIMIT']
  - `Find the absolute difference`
  - `Find which publisher has more superheroes`
- LLM `Output Control: Projection` → ['aggregate:SELECT']
  - `Output only the difference`
  - `Output the counts for each publisher and the difference`
- LLM `Output Control: Row Structure` → ['aggregate:DISTINCT', 'aggregate:GROUP']
  - `Single row with the difference`
  - `Two rows: one for each publisher with their respective counts`

**Hit/Miss/FP:**

- HIT `aggregate:SELECT`: tier=semantic
- MISS axis (not predicted): `combine:BASE`
- HIT `combine:JOINS`: tier=semantic
- HIT `source:FROM`: tier=exact
- FP atomic axis: `aggregate:DISTINCT`
- FP atomic axis: `aggregate:GROUP`
- FP atomic axis: `aggregate:LIMIT`
- FP atomic axis: `aggregate:ORDERBY`
- FP atomic axis: `combine:COMBINE_WHERE`
- FP atomic axis: `filter:WHERE`

**一句话:** axis_recall=0.75, value_semantic=1.0, FP_rate=0.67.

### qid=349

**NL:** Name the card with the most ruling information and its artist. Also state if the card is a promotional printing.

**Gold atomic axes:**

- `aggregate:DISTINCT`: `true`
- `aggregate:GROUP`: `uuid`
- `aggregate:HAVING`: `count(id) = (select max(ruling_count) from (select count(id) as rul...`
- `aggregate:SELECT`: `uuid`
- `combine:BASE`: `source_state_query_1`
- `combine:JOINS`: `{"on": "source_state_query_1.uuid = aggregate_state_query_2.uuid", ...`
- `source:FROM`: `rulings`

**LLM slots → mapped atomic candidates:**

- LLM `Reference Grounding: Table / Column / Join Path ambiguity` → ['aggregate:GROUP', 'aggregate:SELECT', 'combine:JOINS', 'filter:WHERE', 'source:FROM']
  - `rulings.text`
  - `rulings.date`
  - `cards.text`
- LLM `Reference Grounding: Table / Column / Join Path ambiguity` → ['aggregate:GROUP', 'aggregate:SELECT', 'combine:JOINS', 'filter:WHERE', 'source:FROM']
  - `cards.artist`
  - `foreign_data.artist`
- LLM `Reference Grounding: Table / Column / Join Path ambiguity` → ['aggregate:GROUP', 'aggregate:SELECT', 'combine:JOINS', 'filter:WHERE', 'source:FROM']
  - `cards.isPromo`
  - `sets.isFoilOnly`
  - `sets.isForeignOnly`
- LLM `Measure Construction: Formula / Numeric / Boundary ambiguity` → ['combine:COMBINE_WHERE', 'filter:WHERE']
  - `COUNT(rulings.id)`
  - `MAX(rulings.date)`
  - `MAX(LENGTH(rulings.text))`
- LLM `Ranking Target: Extremum / Method / Direction ambiguity` → ['aggregate:LIMIT', 'aggregate:ORDERBY']
  - `ORDER BY COUNT(rulings.id) DESC`
  - `ORDER BY MAX(rulings.date) DESC`
  - `ORDER BY MAX(LENGTH(rulings.text)) DESC`
- LLM `Output Control: Projection / Row Structure ambiguity` → ['aggregate:DISTINCT', 'aggregate:GROUP', 'aggregate:SELECT']
  - `SELECT cards.name, cards.artist`
  - `SELECT cards.asciiName, cards.artist`
- LLM `Output Control: Projection / Row Structure ambiguity` → ['aggregate:DISTINCT', 'aggregate:GROUP', 'aggregate:SELECT']
  - `SELECT cards.isPromo`
  - `SELECT sets.isFoilOnly`
  - `SELECT sets.isForeignOnly`

**Hit/Miss/FP:**

- HIT `aggregate:DISTINCT`: tier=miss
- HIT `aggregate:GROUP`: tier=miss
- MISS axis (not predicted): `aggregate:HAVING`
- HIT `aggregate:SELECT`: tier=miss
- MISS axis (not predicted): `combine:BASE`
- HIT `combine:JOINS`: tier=miss
- HIT `source:FROM`: tier=semantic
- FP atomic axis: `aggregate:LIMIT`
- FP atomic axis: `aggregate:ORDERBY`
- FP atomic axis: `combine:COMBINE_WHERE`
- FP atomic axis: `filter:WHERE`

**一句话:** axis_recall=0.71, value_semantic=0.2, FP_rate=0.44.

### qid=125

**NL:** For loans contracts which are still running where client are in debt, state their district percentage unemployment rate increment from year 1995 to 1996.

**Gold atomic axes:**

- `aggregate:SELECT`: `(district.a13 - district.a12) / district.a12 * 100`
- `combine:BASE`: `source_state_query_1`
- `combine:COMBINE_WHERE`: `loan.status = 'd' and not district.a12 is null and not district.a13...`
- `combine:JOINS`: `{"on": "source_state_query_1.account_id = account.account_id", "tar...`
- `source:FROM`: `loan`

**LLM slots → mapped atomic candidates:**

- LLM `Reference Grounding: Table / Column ambiguity` → ['aggregate:GROUP', 'aggregate:SELECT', 'filter:WHERE']
  - `district.A12`
  - `district.A13`
- LLM `Reference Grounding: Join Path ambiguity` → ['combine:JOINS']
  - `loan.account_id = account.account_id AND account.district_id = distric`
  - `loan.account_id = disp.account_id AND disp.client_id = client.client_i`
- LLM `Value Grounding: Value Encoding ambiguity` → ['filter:WHERE']
  - `loan.status = 'B'`
  - `loan.status = 'D'`
- LLM `Measure Construction: Formula ambiguity` → ['aggregate:SELECT']
  - `(district.A12_1996 - district.A12_1995) / district.A12_1995 * 100`
  - `(district.A13_1996 - district.A13_1995) / district.A13_1995 * 100`
- LLM `Output Control: Projection ambiguity` → ['aggregate:SELECT']
  - `district.A2, district.A12`
  - `district.A2, district.A13`

**Hit/Miss/FP:**

- HIT `aggregate:SELECT`: tier=semantic
- MISS axis (not predicted): `combine:BASE`
- MISS axis (not predicted): `combine:COMBINE_WHERE`
- HIT `combine:JOINS`: tier=semantic
- MISS axis (not predicted): `source:FROM`
- FP atomic axis: `aggregate:GROUP`
- FP atomic axis: `filter:WHERE`

**一句话:** axis_recall=0.40, value_semantic=1.0, FP_rate=0.50.


## Step 7 总判断

**Stage 1 prompt 需要工程改进, 但 framework 可行, 建议先做 prompt iteration** (mid)
