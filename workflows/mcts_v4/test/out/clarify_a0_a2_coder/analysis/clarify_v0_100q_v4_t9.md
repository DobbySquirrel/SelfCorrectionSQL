# AutoClarify v0 — T9 AnswerAgent abstain root cause (v4)

- Generated: 2026-06-04T16:19:04.808508+00:00
- Trace: `clarify_v0_log_only_100q_v4.trace.jsonl`
- Parse OK: 30 | Abstain: 28 | axis=None: 31

## T9.1 Abstain reason breakdown (28 abstain qids)

Non-exclusive flags (one qid may match multiple mandatory rules):

| reason | n |
|---|---:|
| confidence < 0.60 | 28 |
| mandatory: multiple support | 7 |
| mandatory: no verbatim quote | 17 |
| mandatory: domain assumption | 0 |
| empty evidence | 7 |
| LLM abstain unmatched | 7 |

Primary reason (mutually exclusive, priority order):

| primary reason | n |
|---|---:|
| mandatory: no verbatim quote | 10 |
| mandatory: multiple support | 7 |
| empty evidence | 7 |
| confidence < 0.60 (LLM self-rated) | 4 |

Note: all 28 abstain rows have `confidence=0.0` in trace (LLM self-rated below 0.60). Mandatory-rule text in `evidence` explains *why*.

## T9.2 Pool reality on 28 abstain qids

| qid | gold_in_pool | gold_in_top3 | gold_rank | gold_candidate_cid |
|---:|---|---|---:|---|
| 25 | False | False | - | - |
| 31 | False | False | - | - |
| 32 | False | False | - | - |
| 48 | False | False | - | - |
| 50 | False | False | - | - |
| 197 | False | False | - | - |
| 263 | False | False | - | - |
| 530 | False | False | - | - |
| 533 | False | False | - | - |
| 547 | False | False | - | - |
| 557 | False | False | - | - |
| 765 | False | False | - | - |
| 875 | False | False | - | - |
| 915 | False | False | - | - |
| 1011 | False | False | - | - |
| 1037 | False | False | - | - |
| 1136 | False | False | - | - |
| 1229 | False | False | - | - |
| 1238 | False | False | - | - |
| 1256 | False | False | - | - |
| 1275 | False | False | - | - |
| 1338 | False | False | - | - |
| 1389 | False | False | - | - |
| 1401 | False | False | - | - |
| 1422 | False | False | - | - |
| 1486 | False | False | - | - |
| 1498 | False | False | - | - |
| 1531 | False | False | - | - |

- gold_in_pool: **0/28** (0%)
- gold_in_top3: **0/28**
- gold_candidate mappable: **0/28**

## T9.3 Counterfactual ceiling on 28 abstain (oracle answer)

Oracle: pick `gold_candidate_cid`, compile hard constraint, simulate hard prune + R2.

```
n abstain qids = 28
├─ gold not in pool                     = 28
├─ gold in pool but not top3 cluster    = 0
├─ gold in top3, no candidate maps      = 0
├─ oracle no hard / self-check fail     = 0
├─ oracle hurt (constraint excludes gold)= 0
├─ oracle no gain (R2 already hits gold)= 0
├─ oracle no R2 pick (survives but R2≠gold) = 0
└─ **oracle saved**                     = **0**  ← v0 ceiling
```
- oracle saved qids: *(none)*

## T9.4 Non-abstain qids (72, 948)

| qid | choice | conf | gold_in_pool | gold_rank | chosen_rank | gold_in_chosen_cluster | saved |
|---:|---|---:|---|---:|---:|---|---|
| 72 | B | 0.85 | False | - | 2 | False | False |
| 948 | A | 0.85 | False | - | 1 | False | False |

## T9.5 axis=None qids (31 extract failures)

- gold_in_pool: **0/31** (0%)
- gold_in_top3: **0/31**
- counterfactual oracle saved (if extract+oracle): **0/31**

| qid | gold_in_pool | gold_in_top3 | oracle_saved_if_extract |
|---:|---|---|---|
| 36 | False | False | False |
| 145 | False | False | False |
| 186 | False | False | False |
| 201 | False | False | False |
| 213 | False | False | False |
| 234 | False | False | False |
| 346 | False | False | False |
| 347 | False | False | False |
| 349 | False | False | False |
| 371 | False | False | False |
| 563 | False | False | False |
| 640 | False | False | False |
| 685 | False | False | False |
| 772 | False | False | False |
| 788 | False | False | False |
| 822 | False | False | False |
| 894 | False | False | False |
| 901 | False | False | False |
| 904 | False | False | False |
| 959 | False | False | False |
| 1002 | False | False | False |
| 1042 | False | False | False |
| 1166 | False | False | False |
| 1169 | False | False | False |
| 1243 | False | False | False |
| 1254 | False | False | False |
| 1317 | False | False | False |
| 1357 | False | False | False |
| 1359 | False | False | False |
| 1505 | False | False | False |
| 1529 | False | False | False |

## Decision tree (from T9)

| Gate | Value | Verdict |
|---|---|---|
| T9.3 oracle saved / 28 | **0** | 解释 B：候选真不可分辨 / gold 不在 pool — prompt 改不动 saved |
| Recommended action | | **转 R1 regenerate（解 Case A），勿优先调 prompt** |
| T9.5 gold_in_pool / 31 | **0** | extract 31 题主要是 Case A — 不修 extractor |

### Combined read

- Abstain 28/30 全部 confidence=0.0；primary 原因以 **no verbatim quote** / **multiple support** 为主 → prompt 行为符合设计。
- 但 oracle saved 仅 **0/28** → 即使 oracle 也救不了多数 → Case A 主导。
- axis=None 31 题 gold_in_pool **0/31** → extract 修补边际收益 低。
