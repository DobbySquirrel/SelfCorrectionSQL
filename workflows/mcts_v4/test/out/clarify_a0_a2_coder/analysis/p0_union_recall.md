# P0 — Union Recall Ceiling (static, no GPU)

Generated: 2026-06-04T16:38:56
Eval: 0 unique (qid,sql), workers=batch, timeout=0s/sql, 755s

## 1. Pool & Union Recall（最低输出表）

| Pool / Union | Recall |
|---|---:|
| final single-run | **379/498** (76.1%) |
| ef2 single-run (51q rerun only) | **44/498** (8.8%) |
| **final ∪ ef2** | **423/498** (84.9%) |
| calib single-run | **414/498** (83.1%) |
| final ∪ calib | **432/498** (86.7%) |
| ef2 ∪ calib | **414/498** (83.1%) |
| **final ∪ ef2 ∪ calib** | **432/498** (86.7%) |

> ef2 single-run：仅 `v4_ef2_51_rerun` 的 51 题有池；其余 qid 无记录 → recall 为 false。
> final ∪ ef2 应等于 overlay 后的 merged final+ef2 口径。

## 2. Exclusive qids

| 集合 | n | qids |
|---|---:|---|
| **calib_only**（calib ✓, final ✗, ef2 ✗） | 9 | `32, 346, 465, 685, 955, 1238, 1486, 1490, 1505` |
| **final_only**（final ✓, calib ✗, ef2 ✗） | 18 | `26, 31, 72, 186, 219, 234, 530, 637, 694, 726, 788, 868, 915, 1166, 1254, 1270, 1387, 1472` |
| **ef2_only**（ef2 ✓, final ✗, calib ✗） | 0 | `` |
| **missed_by_all** | 66 | `11, 23, 24, 25, 36, 37, 41, 48, 50, 62, 77, 85, 145, 159, 169, 173, 197, 201, 212, 263, 347, 349, 383, 407, 412, 533, 557, 587, 639, 640, 671, 772, 794, 829, 861, 892, 894, 901, 904, 948, 1002, 1011, 1028, 1032, 1037, 1080, 1114, 1136, 1148, 1149, 1169, 1175, 1187, 1227, 1243, 1252, 1256, 1302, 1357, 1359…` |

## 3. Overlap matrix（recall 在池内）

| final | ef2 | calib | n |
|---|---|---|---:|
| ✗ | ✗ | ✗ | 66 |
| ✗ | ✗ | ✓ | 9 |
| ✗ | ✓ | ✗ | 0 |
| ✗ | ✓ | ✓ | 44 |
| ✓ | ✗ | ✗ | 18 |
| ✓ | ✗ | ✓ | 361 |
| ✓ | ✓ | ✗ | 0 |
| ✓ | ✓ | ✓ | 0 |

## 4. 决策（final ∪ ef2 ∪ calib）

⚠️ **432/498** 在 430–439 → 中等；adaptive extra rollout 仍有希望。

静态 pool union；不重跑 GPU。
