# Phase 4b Preflight (4b.1 + 4b.2)

Generated: 2026-06-03T15:28:28

## 4b.2 — R0 replay vs stored final (447 ex-ef2)

| Metric | Value |
|---|---|
| stored `stats.gold_match` Hit@1 | **309/447** (69.1%) |
| R0 replay Hit@1 | **309/447** (69.1%) |
| **Δ replay − stored** | **+0** |

- replay ✓, stored ✗ (**0**): `[]`
- stored ✓, replay ✗ (**0**): `[]`

> **Verdict**: |Δ| ≤ 3 — paper 可写一行 replay vs pipeline post-processing ±noise。

## 4b.1 — Fixed-hurt cluster dump (149 / 352 / 1257)

### qid **149**

| Field | Value |
|---|---|
| baseline Hit@1 | False |
| stored final Hit@1 | True |
| R0 replay Hit@1 | True |
| R2 replay Hit@1 | False |
| max cluster size | 45 |
| R0 sig == R2 sig? | False |

**R0 pick** (sig `e76180fef0bed0a6da3611e2…`):
```sql
WITH account_types_not_eligible AS (
    SELECT DISTINCT dis.type AS account_type
    FROM account a
    JOIN district d ON a.district_id = d.district_id
    JOIN disp dis ON a.account_id = dis.account_id
    WHERE d.A11 > 8000 AND d.A11 <= 9000
    AND dis.type != 'OWNER'
)
SELECT account_type FROM account_types_not_eligible;
```

**R2 pick** (sig `0c0ce486ad2c955a4b587ce2…`):
```sql
WITH final_account_types AS (
    SELECT DISTINCT a.frequency AS account_type
    FROM account a
    INNER JOIN district d ON a.district_id = d.district_id
    WHERE d.A11 > 8000 AND d.A11 <= 9000
    AND a.frequency != 'OWNER'
)
SELECT account_type FROM final_account_types;
```

| rank | size | visit | max_r | variants | sig | rep_sql (120c) |
|---:|---:|---:|---:|---:|---|---|
| 1 | 45 | 3 | 1.00 | 45 | `0c0ce486ad2c955a…` **R2** | `WITH final_account_types AS (
    SELECT DISTINCT a.frequency AS account_type
    FROM account a
    INNER JOIN district` |
| 2 | 30 | 2 | 1.00 | 30 | `e76180fef0bed0a6…` **R0** | `WITH account_types_not_eligible AS (
    SELECT DISTINCT dis.type AS account_type
    FROM account a
    JOIN district d` |
| 3 | 15 | 1 | 1.00 | 15 | `8c413cfd66a26744…` | `WITH cte1 AS (
    SELECT d.district_id, d.A11 AS average_salary
    FROM district d
    WHERE d.A11 > 8000 AND d.A11 <=` |
| 4 | 15 | 1 | 1.00 | 15 | `0309d53bfe407627…` | `WITH cte1 AS (
    SELECT d.district_id, d.A11 AS average_salary
    FROM district d
    WHERE d.A11 > 8000 AND d.A11 <=` |

- **Hypothesis tag**: R0 选**较小 cluster 但正确**；R2 偏向大 cluster（设计取舍）

### qid **352**

| Field | Value |
|---|---|
| baseline Hit@1 | False |
| stored final Hit@1 | True |
| R0 replay Hit@1 | True |
| R2 replay Hit@1 | True |
| max cluster size | 60 |
| R0 sig == R2 sig? | True |

**R0 pick** (sig `e44f0e33ded91861456a1b12…`):
```sql
WITH cte1 AS (
    SELECT COUNT(*) AS total_cards
    FROM cards
),
cte2 AS (
    SELECT COUNT(*) AS chinese_simplified_cards
    FROM foreign_data
    WHERE language = 'Chinese Simplified'
)
SELECT 
    (cte2.chinese_simplified_cards * 100.0 / cte1.total_cards) AS percentage
FROM cte1, cte2;
```

**R2 pick** (sig `e44f0e33ded91861456a1b12…`):
```sql
WITH cte1 AS (
    SELECT COUNT(*) AS total_cards
    FROM cards
),
cte2 AS (
    SELECT COUNT(*) AS chinese_simplified_cards
    FROM foreign_data
    WHERE language = 'Chinese Simplified'
)
SELECT 
    (cte2.chinese_simplified_cards * 100.0 / cte1.total_cards) AS percentage
FROM cte1, cte2;
```

| rank | size | visit | max_r | variants | sig | rep_sql (120c) |
|---:|---:|---:|---:|---:|---|---|
| 1 | 60 | 4 | 1.00 | 60 | `1d3d6b3227bb75bf…` | `WITH chinese_simplified_count AS (
    SELECT COUNT(*) AS chinese_simplified_cards
    FROM foreign_data
    WHERE langu` |
| 2 | 60 | 10 | 1.00 | 60 | `e44f0e33ded91861…` **R0** **R2** | `WITH cte1 AS (
    SELECT COUNT(*) AS total_cards
    FROM cards
),
cte2 AS (
    SELECT COUNT(*) AS chinese_simplified_` |


### qid **1257**

| Field | Value |
|---|---|
| baseline Hit@1 | False |
| stored final Hit@1 | True |
| R0 replay Hit@1 | True |
| R2 replay Hit@1 | False |
| max cluster size | 75 |
| R0 sig == R2 sig? | True |

**R0 pick** (sig `c593effd31862d7797553418…`):
```sql
WITH abnormal_creatinine_patients AS (
    SELECT DISTINCT l.ID
    FROM Laboratory l
    WHERE l.CRE >= 1.5
)
SELECT COUNT(*) AS count_under_70
FROM abnormal_creatinine_patients acp
JOIN Patient p ON acp.ID = p.ID
WHERE CAST(strftime('%Y', 'now') AS INTEGER) - CAST(strftime('%Y', p.Birthday) AS INTEGER) < 70
```

**R2 pick** (sig `c593effd31862d7797553418…`):
```sql
WITH cte1 AS (
    SELECT p.ID, p.Birthday, l.CRE
    FROM Patient p
    INNER JOIN Laboratory l ON p.ID = l.ID
    WHERE l.CRE >= 1.5
),
cte2 AS (
    SELECT ID, Birthday, CRE,
           CAST(strftime('%Y', 'now') AS INTEGER) - CAST(strftime('%Y', Birthday) AS INTEGER) AS age
    FROM cte1
),
cte3 AS (
    SELECT COUNT(*) AS answer
    FROM cte2
    WHERE age < 70
)
SELECT answer
FROM cte3;
```

| rank | size | visit | max_r | variants | sig | rep_sql (120c) |
|---:|---:|---:|---:|---:|---|---|
| 1 | 75 | 5 | 1.00 | 75 | `c593effd31862d77…` **R0** **R2** | `WITH cte1 AS (
    SELECT p.ID, p.Birthday, l.CRE
    FROM Patient p
    INNER JOIN Laboratory l ON p.ID = l.ID
    WHER` |
| 2 | 30 | 2 | 1.00 | 30 | `942fea1cbf269da2…` | `WITH abnormal_creatinine_patients AS (
    SELECT DISTINCT l.ID
    FROM Laboratory l
    WHERE l.CRE >= 1.5
),
patients` |
| 3 | 15 | 1 | 1.00 | 15 | `3bcf183271027ff3…` | `WITH abnormal_creatinine_patients AS (
    SELECT DISTINCT l.ID
    FROM Laboratory l
    WHERE l.CRE >= 1.5
)
SELECT CO` |


---

**🛑 STOP (4b.1+4b.2)** — 审后再决定是否加 R2' fallback 与 4b.3 patch。
