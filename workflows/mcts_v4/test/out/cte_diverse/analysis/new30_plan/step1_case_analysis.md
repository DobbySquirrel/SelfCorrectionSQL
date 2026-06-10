# Step 1: D hurt + B saved case analysis

Generated: 2026-06-09T11:11:19.103202+00:00

## D hurt

### qid=901 — gold_lost_from_pool

- E0: recall=True hit_r3=False gold_rank=3
- E1: recall=False hit_r3=False gold_rank=None
- Plans with gold in pool: []

**E0 top clusters (R3 rank)**

| rank | sig | visit | count | r3_score | gold? |
|---:|---|---:|---:|---:|:---:|
| 1 | 37f7232fbd7ef275 | 0 | 45 | 45.0 | 0 |
| 2 | 259b075ec4ff3dde | 0 | 13 | 13.0 | 0 |
| 3 | 7430d474081d70ec | 0 | 2 | 1.6 | 1 |

**E1 union top clusters**

| rank | sig | visit | count | r3_score | gold? |
|---:|---|---:|---:|---:|:---:|
| 1 | 37f7232fbd7ef275 | 0 | 60 | 60.0 | 0 |

**E1 per-plan gold**

- `plan_measure`: recall=False gold_rank=None top1_gold=False gold_visit=0
- `plan_output`: recall=False gold_rank=None top1_gold=False gold_visit=0
- `plan_relation`: recall=False gold_rank=None top1_gold=False gold_visit=0

### qid=948 — gold_lost_from_pool

- E0: recall=True hit_r3=False gold_rank=3
- E1: recall=False hit_r3=False gold_rank=None
- Plans with gold in pool: []

**E0 top clusters (R3 rank)**

| rank | sig | visit | count | r3_score | gold? |
|---:|---|---:|---:|---:|:---:|
| 1 | 66b0ed2488e52bfb | 0 | 34 | 34.0 | 0 |
| 2 | d798e91d46257033 | 0 | 11 | 11.0 | 0 |
| 3 | f839c517829fefcc | 0 | 7 | 7.0 | 1 |
| 4 | 9eea3075c179680e | 0 | 4 | 3.2 | 0 |
| 5 | 5c6ba9094b82fbef | 0 | 3 | 1.8 | 1 |
| 6 | 3dc5e92a689a820e | 0 | 1 | 0.8 | 0 |

**E1 union top clusters**

| rank | sig | visit | count | r3_score | gold? |
|---:|---|---:|---:|---:|:---:|
| 1 | d798e91d46257033 | 0 | 23 | 23.0 | 0 |
| 2 | 9eea3075c179680e | 0 | 17 | 17.0 | 0 |
| 3 | 66b0ed2488e52bfb | 0 | 15 | 15.0 | 0 |
| 4 | 0f048fdb6af21c23 | 0 | 5 | 5.0 | 0 |

**E1 per-plan gold**

- `plan_measure`: recall=False gold_rank=None top1_gold=False gold_visit=0
- `plan_output`: recall=False gold_rank=None top1_gold=False gold_visit=0
- `plan_relation`: recall=False gold_rank=None top1_gold=False gold_visit=0

## B saved (Hit@1)

### qid=424 — support_aggregation_multi_plan

- E0: recall=True hit_r3=False gold_rank=2
- E1: recall=True hit_r3=True gold_rank=1
- Plans with gold in pool: ['plan_output', 'plan_relation']

**E0 top clusters (R3 rank)**

| rank | sig | visit | count | r3_score | gold? |
|---:|---|---:|---:|---:|:---:|
| 1 | c7ae49e9892bed91 | 0 | 30 | 30.0 | 0 |
| 2 | dab5ffbf133f45f1 | 0 | 30 | 30.0 | 1 |

**E1 union top clusters**

| rank | sig | visit | count | r3_score | gold? |
|---:|---|---:|---:|---:|:---:|
| 1 | dab5ffbf133f45f1 | 0 | 32 | 32.0 | 1 |
| 2 | c7ae49e9892bed91 | 0 | 28 | 28.0 | 0 |

**E1 per-plan gold**

- `plan_measure`: recall=False gold_rank=None top1_gold=False gold_visit=0
- `plan_output`: recall=True gold_rank=1 top1_gold=True gold_visit=0
- `plan_relation`: recall=True gold_rank=1 top1_gold=True gold_visit=0

### qid=758 — support_aggregation_multi_plan

- E0: recall=True hit_r3=False gold_rank=2
- E1: recall=True hit_r3=True gold_rank=1
- Plans with gold in pool: ['plan_measure', 'plan_output', 'plan_relation']

**E0 top clusters (R3 rank)**

| rank | sig | visit | count | r3_score | gold? |
|---:|---|---:|---:|---:|:---:|
| 1 | 5938b5189f28ba66 | 0 | 23 | 23.0 | 0 |
| 2 | 97e5febe5f43a5fc | 0 | 22 | 22.0 | 1 |
| 3 | d64839514a9720f5 | 0 | 15 | 15.0 | 1 |

**E1 union top clusters**

| rank | sig | visit | count | r3_score | gold? |
|---:|---|---:|---:|---:|:---:|
| 1 | eb7de8f2f5d1aa3b | 0 | 26 | 26.0 | 1 |
| 2 | 97e5febe5f43a5fc | 0 | 24 | 24.0 | 1 |
| 3 | 9a5a9736ccf34f53 | 0 | 5 | 5.0 | 1 |
| 4 | 5938b5189f28ba66 | 0 | 4 | 3.2 | 0 |
| 5 | d64839514a9720f5 | 0 | 1 | 0.8 | 1 |

**E1 per-plan gold**

- `plan_measure`: recall=True gold_rank=1 top1_gold=True gold_visit=0
- `plan_output`: recall=True gold_rank=1 top1_gold=True gold_visit=0
- `plan_relation`: recall=True gold_rank=1 top1_gold=True gold_visit=0

### qid=915 — support_aggregation_multi_plan

- E0: recall=True hit_r3=False gold_rank=2
- E1: recall=True hit_r3=True gold_rank=1
- Plans with gold in pool: ['plan_measure', 'plan_output', 'plan_relation']

**E0 top clusters (R3 rank)**

| rank | sig | visit | count | r3_score | gold? |
|---:|---|---:|---:|---:|:---:|
| 1 | d88b952b8ec20078 | 0 | 45 | 45.0 | 0 |
| 2 | 67a34dbac35f05dc | 0 | 15 | 15.0 | 1 |

**E1 union top clusters**

| rank | sig | visit | count | r3_score | gold? |
|---:|---|---:|---:|---:|:---:|
| 1 | 67a34dbac35f05dc | 0 | 31 | 31.0 | 1 |
| 2 | d88b952b8ec20078 | 0 | 19 | 19.0 | 0 |

**E1 per-plan gold**

- `plan_measure`: recall=True gold_rank=1 top1_gold=True gold_visit=0
- `plan_output`: recall=True gold_rank=1 top1_gold=True gold_visit=0
- `plan_relation`: recall=True gold_rank=2 top1_gold=False gold_visit=0

### qid=1029 — support_aggregation_multi_plan

- E0: recall=True hit_r3=False gold_rank=2
- E1: recall=True hit_r3=True gold_rank=1
- Plans with gold in pool: ['plan_measure', 'plan_output', 'plan_relation']

**E0 top clusters (R3 rank)**

| rank | sig | visit | count | r3_score | gold? |
|---:|---|---:|---:|---:|:---:|
| 1 | 4f9d0ff84e6494d8 | 0 | 27 | 27.0 | 0 |
| 2 | a41a2567e12026b5 | 0 | 14 | 14.0 | 1 |
| 3 | 078b2e4615fd8942 | 0 | 13 | 13.0 | 1 |
| 4 | f1926ad1fe3b9325 | 0 | 5 | 5.0 | 1 |
| 5 | fdf975f29ad641da | 0 | 1 | 0.8 | 0 |

**E1 union top clusters**

| rank | sig | visit | count | r3_score | gold? |
|---:|---|---:|---:|---:|:---:|
| 1 | a41a2567e12026b5 | 0 | 24 | 24.0 | 1 |
| 2 | 4f9d0ff84e6494d8 | 0 | 18 | 18.0 | 0 |
| 3 | f1926ad1fe3b9325 | 0 | 14 | 14.0 | 1 |
| 4 | fdf975f29ad641da | 0 | 2 | 0.8 | 0 |
| 5 | 03d7ff83832cda7c | 0 | 1 | 0.6 | 1 |
| 6 | fc1f5bc18e574e84 | 0 | 1 | 0.4 | 0 |

**E1 per-plan gold**

- `plan_measure`: recall=True gold_rank=1 top1_gold=True gold_visit=0
- `plan_output`: recall=True gold_rank=2 top1_gold=False gold_visit=0
- `plan_relation`: recall=True gold_rank=1 top1_gold=True gold_visit=0

### qid=1235 — support_aggregation_multi_plan

- E0: recall=True hit_r3=False gold_rank=2
- E1: recall=True hit_r3=True gold_rank=1
- Plans with gold in pool: ['plan_measure', 'plan_output', 'plan_relation']

**E0 top clusters (R3 rank)**

| rank | sig | visit | count | r3_score | gold? |
|---:|---|---:|---:|---:|:---:|
| 1 | 31d5e506855a690f | 0 | 27 | 27.0 | 0 |
| 2 | 52ead7c76ee5d815 | 0 | 23 | 23.0 | 1 |
| 3 | a87dd4944b24d897 | 0 | 5 | 5.0 | 0 |
| 4 | 0fa62c0e8656f479 | 0 | 2 | 0.8 | 0 |
| 5 | def85e0d2a9346d0 | 0 | 2 | 0.8 | 0 |
| 6 | cb17f52468cb1350 | 0 | 1 | 0.4 | 0 |

**E1 union top clusters**

| rank | sig | visit | count | r3_score | gold? |
|---:|---|---:|---:|---:|:---:|
| 1 | 52ead7c76ee5d815 | 0 | 23 | 23.0 | 1 |
| 2 | 0fa62c0e8656f479 | 0 | 22 | 22.0 | 0 |
| 3 | def85e0d2a9346d0 | 0 | 10 | 10.0 | 0 |
| 4 | 31d5e506855a690f | 0 | 4 | 3.2 | 0 |
| 5 | 023720c10a383013 | 0 | 1 | 0.6 | 0 |

**E1 per-plan gold**

- `plan_measure`: recall=True gold_rank=3 top1_gold=False gold_visit=0
- `plan_output`: recall=True gold_rank=1 top1_gold=True gold_visit=0
- `plan_relation`: recall=True gold_rank=1 top1_gold=True gold_visit=0

