# A2c Hash paired diff (30q)

- Expansion steps: 627
- Cluster count legacy sum: 995
- Cluster count v2 sum: 993
- Merge events (legacy split → v2 merge): 2
- Split events (legacy merge → v2 split): 0

## Merge examples
[
  {
    "qid": "1482",
    "step": "1::1",
    "legacy_n": 3,
    "v2_n": 2
  },
  {
    "qid": "1490",
    "step": "13::3",
    "legacy_n": 2,
    "v2_n": 1
  }
]

## Split examples
[]

> **Split=0 解释**：Split=0 是 A0 阶段 v2 仅作为 post-hoc 标注的结构性预期：节点的合并由 legacy 决定，v2 只在已合并节点上事后标注，因此 v2 在不改变 legacy 节点边界的前提下产生不了 split。要观察 v2 真实的 split 行为，需 Task 7（v2 切搜索分桶）后重新对比。

---

## Manual Inspection

### Merge case 1: qid=1482, step 1::1

**Legacy buckets**: 3  
**v2 buckets**: 2  
**Merged pair**: bucket_0 + bucket_1 → v2 `48a0c25e3e58858b...`（cluster_id=0）

**Bucket 0 execution result (legacy)** — `4_3_f1ba18ad...`:

| Segment | Year | TotalConsumption |
|---------|------|------------------|
| LAM | 2012 | 528.3 |
| LAM | 2013 | 5026.78 |
| SME | 2012 | 144.07 |
| SME | 2013 | 3789.55 |

**Bucket 1 execution result (legacy)** — `4_3_4e31262e...`:

| Segment | Year | TotalConsumption |
|---------|------|------------------|
| LAM | 2012 | 528.3 |
| LAM | 2013 | 5026.78 |
| SME | 2012 | 144.07 |
| SME | 2013 | 3789.55 |

（Bucket 1 的 `Year` 列为字符串 `'2012'`/`'2013'`，Bucket 0 为整型 `2012`/`2013`，数值相同。）

**Bucket 2（未合并，legacy/v2 均独立）** — 列名 `Consumption`（非 TotalConsumption），且 2013 年 LAM/SME 为多行明细而非聚合，与 bucket 0/1 语义不同。

**Why legacy judged different**: Bucket 0 vs 1 行集合在 legacy top-5 hash 中因 **Year 列 int vs str 类型** 被拆成两个 signature。  
**Why v2 merged**: v2 `normalize=True` 将单元格统一为字符串后，**行集合完全相同**，合并为同一 cluster。  
**Manual judgement**: ✅ 合理 — 执行结果语义相同，legacy 因类型差异过度分裂。

---

### Merge case 2: qid=1490, step 13::3

**Legacy buckets**: 2  
**v2 buckets**: 1  
**Merged pair**: bucket_0 + bucket_1 → v2 `d9d44abf50ae72c3...`（cluster_id=0）

**Bucket 0 execution result (legacy)** — `1_1_68e7bd91...`:

| percentage |
|------------|
| 98.44906591469862 |

**Bucket 1 execution result (legacy)** — `1_1_52fba23d...`:

| percentage |
|------------|
| 98.44906591469864 |

**Why legacy judged different**: 两 bucket 仅 **浮点末位差异**（`...862` vs `...864`），legacy hash 对原始 float 字符串敏感。  
**Why v2 merged**: v2 `normalize` 将 float **截断到 6 位小数**（`98.449066`），两值相同。  
**Manual judgement**: ✅ 合理 — 亚精度级差异不应产生 sibling cluster；6 位精度对 percentage 足够。
