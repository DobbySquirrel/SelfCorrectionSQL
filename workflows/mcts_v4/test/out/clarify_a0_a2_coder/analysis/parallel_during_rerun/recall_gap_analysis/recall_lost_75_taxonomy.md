# Recall-lost 75 — 7-bucket taxonomy (R1)

Generated: 2026-06-03T22:01:11

**Pool**: 498 merged (ef2 rerun overlay), oracle recall=False → **75** questions.

## 1. 主表分布

| Bucket | n | % | 修复方向 | 动 H1? | 动 30q? | paper? |
|---|---:|---:|---|---|---|---|
| **S6** | 4 | 5.3% | paper §6 ceiling | no | no | yes limitation |
| **S4** | 18 | 24.0% | DDL trim / schema linker | maybe | no | infra |
| **S3** | 1 | 1.3% | prompt / evidence | maybe | teacher? | prompt |
| **S1** | 0 | 0.0% | max_depth↑ | yes | 30q | H1 param |
| **S2** | 0 | 0.0% | K↑ / temperature | yes | 30q | search div |
| **S7** | 41 | 54.7% | reward redesign | yes | teacher | core |
| **S5** | 9 | 12.0% | reward + cluster | yes | teacher | selection |
| **S0** | 2 | 2.7% | manual review | ? | ? | residual |

## 2. db_id / complexity

- **S6** (4): db top3 [('european_football_2', 2), ('california_schools', 1), ('debit_card_specializing', 1)]; complexity {'simple': 2, 'medium': 1, 'complex': 1}
- **S4** (18): db top3 [('california_schools', 7), ('financial', 2), ('formula_1', 2)]; complexity {'simple': 8, 'medium': 6, 'complex': 4}
- **S3** (1): db top3 [('toxicology', 1)]; complexity {'complex': 1}
- **S7** (41): db top3 [('thrombosis_prediction', 6), ('debit_card_specializing', 6), ('codebase_community', 5)]; complexity {'simple': 22, 'medium': 17, 'complex': 2}
- **S5** (9): db top3 [('card_games', 2), ('codebase_community', 2), ('formula_1', 2)]; complexity {'simple': 7, 'medium': 2}
- **S0** (2): db top3 [('card_games', 2)]; complexity {'simple': 2}

## 3. S0 残差清单

`['346', '407']`

## 4. Baseline 交叉（Qwen r=20 legacy）

- recall-lost 中 baseline Hit@1: **9**
- baseline 有 recall 但我们仍 lost: **31**
- 双方均无 recall: **44**

## 5. 决策小结

- 主导桶: **S7** (41), **S4** (18), **S5** (9)
- ⚠️ **S5+S7 = 50 (≥30)** → reward redesign 主路径

