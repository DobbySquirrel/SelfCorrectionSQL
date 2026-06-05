# Hash precision deep dive (1257 / 801 / 897)

> final_498 跑时 `MCTS_USE_SIGNATURE_V2=1` → stored `result_signature` = **v2** (`create_result_signature_v2`).

## qid 1257

- stored bucket sig (legacy key): `c593effd31862d7797553418e520e00e…`
- R0 hit: True | R2 hit: False

- **R0_pick**: hit=True n_rows=1 cols=['count_under_70'] legacy=`1_1_e79248baea0e…` v2=`3bcf183271027ff3…`
- **R2_pick**: hit=False n_rows=1 cols=['answer'] legacy=`1_1_e04937984540…` v2=`c593effd31862d77…`
- **gold**: hit=True n_rows=1 cols=['COUNT(DISTINCT T1.ID)'] legacy=`1_1_e79248baea0e…` v2=`dbd4505d850ba820…`

### Distinct SQL in merged cluster (re-exec signatures)

- hit=False rows=1 stored_v2=True v2==R0pick=False legacy==stored=False
  legacy=`1_1_e0493798454082245e8b268d7f44f810` v2=`c593effd31862d7797553418e520e00e`
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
- hit=False rows=1 stored_v2=True v2==R0pick=False legacy==stored=False
  legacy=`1_1_e0493798454082245e8b268d7f44f810` v2=`c593effd31862d7797553418e520e00e`
```sql
WITH cte1 AS (
    SELECT p.ID, p.Birthday, l.CRE
    FROM Patient p
    INNER JOIN Laboratory l ON p.ID = l.ID
),
cte2 AS (
    SELECT ID, Birthday, CRE
    FROM cte1
    WHERE CRE >= 1.5
),
cte3 AS (
    SELECT ID, Birthday, CRE,
           CAST(strftime('%Y', 'now') AS INTEGER) - CAST(strftime('%Y', Birthday) AS INTEGER) AS Age
    FROM cte2
),
cte4 AS (
    SELECT COUNT(*) AS answer
    FROM cte3
    WHERE Age < 70
)
SELECT answer
FROM cte4;
```
- hit=False rows=1 stored_v2=True v2==R0pick=False legacy==stored=False
  legacy=`1_1_e0493798454082245e8b268d7f44f810` v2=`c593effd31862d7797553418e520e00e`
```sql
WITH cte1 AS (
    SELECT p.ID, p.Birthday, l.CRE
    FROM Patient p
    INNER JOIN Laboratory l ON p.ID = l.ID
    WHERE l.CRE >= 1.5
),
cte2 AS (
    SELECT ID, Birthday, CRE,
           CAST(strftime('%Y', 'now') AS INTEGER) - CAST(strftime('%Y', Birthday) AS INTEGER) AS Age
    FROM cte1
),
final_count AS (
    SELECT COUNT(*) AS answer
    FROM cte2
    WHERE Age < 70
)
SELECT answer FROM final_count;
```
- hit=False rows=1 stored_v2=True v2==R0pick=False legacy==stored=False
  legacy=`1_1_e0493798454082245e8b268d7f44f810` v2=`c593effd31862d7797553418e520e00e`
```sql
WITH patients_under_70_with_abnormal_creatinine AS (
    SELECT COUNT(*) AS answer
    FROM (
        SELECT p.ID, p.Birthday, l.CRE,
               CAST(strftime('%Y', 'now') AS INTEGER) - CAST(strftime('%Y', Birthday) AS INTEGER) AS Age
        FROM Patient p
        INNER JOIN Laboratory l ON p.ID = l.ID
        WHERE l.CRE >= 1.5
    ) AS filtered_patients
    WHERE Age < 70
)
SELECT answer FROM patients_under_70_with_abnormal_creatinine;
```

## qid 801

- stored bucket sig (legacy key): `979bfacb1f12f2ac8112522bffa14890…`
- R0 hit: True | R2 hit: False

- **R0_pick**: hit=True n_rows=1 cols=['male_to_female_ratio'] legacy=`1_1_7795be2ecb8f…` v2=`4b02a106628b6265…`
- **R2_pick**: hit=False n_rows=1 cols=['ratio'] legacy=`1_1_703cf3bb4028…` v2=`979bfacb1f12f2ac…`
- **gold**: hit=True n_rows=1 cols=["CAST(COUNT(CASE WHEN T2.gender = 'Male' THEN T1.id ELSE NULL END) AS REAL) / COUNT(CASE WHEN T2.gender = 'Female' THEN T1.id ELSE NULL END)"] legacy=`1_1_7795be2ecb8f…` v2=`d5446203e8c5804f…`

### Distinct SQL in merged cluster (re-exec signatures)

- hit=False rows=1 stored_v2=True v2==R0pick=False legacy==stored=False
  legacy=`1_1_703cf3bb4028027a45e5e2d4f4ed4795` v2=`979bfacb1f12f2ac8112522bffa14890`
```sql
WITH male_count AS (
    SELECT COUNT(*) AS male_heroes
    FROM superhero
    WHERE gender_id = 2
),
female_count AS (
    SELECT COUNT(*) AS female_heroes
    FROM superhero
    WHERE gender_id = 1
),
ratio_calc AS (
    SELECT 
        CAST(m.male_heroes AS FLOAT) / CAST(f.female_heroes AS FLOAT) AS ratio
    FROM male_count m
    CROSS JOIN female_count f
)
SELECT ratio
FROM ratio_calc;
```

## qid 897

- stored bucket sig (legacy key): `b0f21e2936fe48ea76b69bd3839f925d…`
- R0 hit: True | R2 hit: False

- **R0_pick**: hit=True n_rows=1 cols=['forename', 'surname', 'nationality', 'max_point_scores'] legacy=`1_4_00b98310f1ef…` v2=`3ab11bbb717f0e19…`
- **R2_pick**: hit=False n_rows=1 cols=['driver_name', 'nationality', 'max_points'] legacy=`1_3_1d34187144fd…` v2=`b0f21e2936fe48ea…`
- **gold**: hit=True n_rows=1 cols=['forename', 'surname', 'nationality', 'MAX(T2.points)'] legacy=`1_4_00b98310f1ef…` v2=`69518d0aa1f26f15…`

### Distinct SQL in merged cluster (re-exec signatures)

- hit=False rows=1 stored_v2=True v2==R0pick=False legacy==stored=False
  legacy=`1_3_1d34187144fdb0e8fd84e7c6047beae1` v2=`b0f21e2936fe48ea76b69bd3839f925d`
```sql
WITH driver_wins AS (
    SELECT 
        ds.driverId,
        COUNT(ds.wins) as total_wins,
        MAX(ds.points) as max_points
    FROM driverStandings ds
    GROUP BY ds.driverId
),
driver_with_most_wins AS (
    SELECT 
        dw.driverId,
        dw.total_wins,
        dw.max_points
    FROM driver_wins dw
    WHERE dw.total_wins = (SELECT MAX(total_wins) FROM driver_wins)
),
driver_info AS (
    SELECT 
        d.driverId,
        d.forename,
        d.surname,
        d.nationality
    FROM drivers d
    WHERE d.driverId = (SELECT driverId FROM driver_with_most_wins)
),
final_driver_i
```
- hit=False rows=1 stored_v2=True v2==R0pick=False legacy==stored=False
  legacy=`1_3_1d34187144fdb0e8fd84e7c6047beae1` v2=`b0f21e2936fe48ea76b69bd3839f925d`
```sql
WITH driver_with_most_wins AS (
    SELECT d.driverId, d.forename, d.surname, d.nationality, SUM(ds.wins) AS total_wins
    FROM drivers d
    JOIN driverStandings ds ON d.driverId = ds.driverId
    GROUP BY d.driverId, d.forename, d.surname, d.nationality
    ORDER BY total_wins DESC
    LIMIT 1
),
driver_max_points AS (
    SELECT d.driverId, MAX(ds.points) AS max_points
    FROM drivers d
    JOIN driverStandings ds ON d.driverId = ds.driverId
    WHERE d.driverId = (SELECT driverId FROM driver_with_most_wins)
    GROUP BY d.driverId
)
SELECT 
    dw.forename || ' ' || dw.surname AS driver_
```
- hit=False rows=1 stored_v2=True v2==R0pick=False legacy==stored=False
  legacy=`1_3_1d34187144fdb0e8fd84e7c6047beae1` v2=`b0f21e2936fe48ea76b69bd3839f925d`
```sql
WITH driver_with_most_wins AS (
    SELECT d.driverId, d.forename, d.surname, d.nationality, SUM(ds.wins) AS total_wins
    FROM drivers d
    JOIN driverStandings ds ON d.driverId = ds.driverId
    GROUP BY d.driverId, d.forename, d.surname, d.nationality
    ORDER BY total_wins DESC
    LIMIT 1
)
SELECT dwm.forename || ' ' || dwm.surname AS driver_name, 
       dwm.nationality, 
       MAX(ds.points) AS max_points
FROM driver_with_most_wins dwm
JOIN driverStandings ds ON dwm.driverId = ds.driverId
GROUP BY dwm.driverId, dwm.forename, dwm.surname, dwm.nationality
ORDER BY max_points DESC
LIMI
```
- hit=False rows=1 stored_v2=True v2==R0pick=False legacy==stored=False
  legacy=`1_3_1d34187144fdb0e8fd84e7c6047beae1` v2=`b0f21e2936fe48ea76b69bd3839f925d`
```sql
WITH driver_with_most_wins AS (
    SELECT d.driverId, d.forename, d.surname, d.nationality, SUM(ds.wins) AS total_wins
    FROM drivers d
    JOIN driverStandings ds ON d.driverId = ds.driverId
    GROUP BY d.driverId, d.forename, d.surname, d.nationality
    ORDER BY total_wins DESC
    LIMIT 1
)
SELECT 
    dw.forename || ' ' || dw.surname AS driver_name,
    dw.nationality,
    MAX(ds.points) AS max_points
FROM driver_with_most_wins dw
JOIN driverStandings ds ON dw.driverId = ds.driverId
GROUP BY dw.driverId, dw.forename, dw.surname, dw.nationality
ORDER BY max_points DESC
LIMIT 1;
```
- hit=False rows=1 stored_v2=True v2==R0pick=False legacy==stored=False
  legacy=`1_3_1d34187144fdb0e8fd84e7c6047beae1` v2=`b0f21e2936fe48ea76b69bd3839f925d`
```sql
WITH driver_with_most_wins AS (
    SELECT d.driverId, d.forename, d.surname, d.nationality, SUM(ds.wins) AS total_wins
    FROM drivers d
    JOIN driverStandings ds ON d.driverId = ds.driverId
    GROUP BY d.driverId, d.forename, d.surname, d.nationality
    ORDER BY total_wins DESC
    LIMIT 1
),
driver_max_points AS (
    SELECT d.driverId, d.forename, d.surname, d.nationality, MAX(ds.points) AS max_points
    FROM drivers d
    JOIN driverStandings ds ON d.driverId = ds.driverId
    WHERE d.driverId = (SELECT driverId FROM driver_with_most_wins)
    GROUP BY d.driverId, d.forename, d.surn
```
- hit=False rows=1 stored_v2=True v2==R0pick=False legacy==stored=False
  legacy=`1_3_1d34187144fdb0e8fd84e7c6047beae1` v2=`b0f21e2936fe48ea76b69bd3839f925d`
```sql
WITH driver_wins AS (
    SELECT 
        ds.driverId,
        COUNT(ds.wins) as total_wins,
        MAX(ds.points) as max_points
    FROM driverStandings ds
    GROUP BY ds.driverId
),
driver_with_most_wins AS (
    SELECT 
        dw.driverId,
        dw.total_wins,
        dw.max_points
    FROM driver_wins dw
    WHERE dw.total_wins = (SELECT MAX(total_wins) FROM driver_wins)
),
final_driver_info AS (
    SELECT 
        CONCAT(d.forename, ' ', d.surname) AS driver_name,
        d.nationality,
        dw.max_points
    FROM drivers d
    INNER JOIN driver_with_most_wins dw ON d.driverId = 
```
- hit=False rows=1 stored_v2=True v2==R0pick=False legacy==stored=False
  legacy=`1_3_1d34187144fdb0e8fd84e7c6047beae1` v2=`b0f21e2936fe48ea76b69bd3839f925d`
```sql
WITH driver_wins AS (
    SELECT d.driverId, d.forename, d.surname, d.nationality, SUM(ds.wins) AS total_wins
    FROM drivers d
    JOIN driverStandings ds ON d.driverId = ds.driverId
    GROUP BY d.driverId, d.forename, d.surname, d.nationality
    ORDER BY total_wins DESC
    LIMIT 1
),
driver_max_points AS (
    SELECT d.driverId, MAX(ds.points) AS max_points
    FROM drivers d
    JOIN driverStandings ds ON d.driverId = ds.driverId
    WHERE d.driverId = (SELECT driverId FROM driver_wins LIMIT 1)
    GROUP BY d.driverId
),
driver_info AS (
    SELECT dw.forename, dw.surname, dw.nationalit
```
- hit=False rows=1 stored_v2=True v2==R0pick=False legacy==stored=False
  legacy=`1_3_1d34187144fdb0e8fd84e7c6047beae1` v2=`b0f21e2936fe48ea76b69bd3839f925d`
```sql
WITH driver_wins AS (
    SELECT d.driverId, d.forename, d.surname, d.nationality, SUM(ds.wins) AS total_wins
    FROM drivers d
    JOIN driverStandings ds ON d.driverId = ds.driverId
    GROUP BY d.driverId, d.forename, d.surname, d.nationality
    ORDER BY total_wins DESC
    LIMIT 1
),
driver_max_points AS (
    SELECT d.driverId, MAX(ds.points) AS max_points
    FROM drivers d
    JOIN driverStandings ds ON d.driverId = ds.driverId
    WHERE d.driverId = (SELECT driverId FROM driver_wins LIMIT 1)
    GROUP BY d.driverId
),
driver_info AS (
    SELECT dw.forename, dw.surname, dw.nationalit
```
- hit=False rows=1 stored_v2=True v2==R0pick=False legacy==stored=False
  legacy=`1_3_1d34187144fdb0e8fd84e7c6047beae1` v2=`b0f21e2936fe48ea76b69bd3839f925d`
```sql
WITH driver_wins AS (
    SELECT d.driverId, d.forename, d.surname, d.nationality, SUM(ds.wins) AS total_wins
    FROM drivers d
    JOIN driverStandings ds ON d.driverId = ds.driverId
    GROUP BY d.driverId, d.forename, d.surname, d.nationality
    ORDER BY total_wins DESC
    LIMIT 1
),
driver_max_points AS (
    SELECT d.driverId, MAX(ds.points) AS max_points
    FROM drivers d
    JOIN driverStandings ds ON d.driverId = ds.driverId
    WHERE d.driverId = (SELECT driverId FROM driver_wins LIMIT 1)
    GROUP BY d.driverId
),
driver_info AS (
    SELECT dw.forename, dw.surname, dw.nationalit
```
- hit=False rows=1 stored_v2=True v2==R0pick=False legacy==stored=False
  legacy=`1_3_1d34187144fdb0e8fd84e7c6047beae1` v2=`b0f21e2936fe48ea76b69bd3839f925d`
```sql
WITH driver_wins AS (
    SELECT d.driverId, d.forename, d.surname, d.nationality, SUM(ds.wins) AS total_wins
    FROM drivers d
    JOIN driverStandings ds ON d.driverId = ds.driverId
    GROUP BY d.driverId, d.forename, d.surname, d.nationality
    ORDER BY total_wins DESC
    LIMIT 1
),
driver_max_points AS (
    SELECT d.driverId, MAX(ds.points) AS max_points
    FROM drivers d
    JOIN driverStandings ds ON d.driverId = ds.driverId
    WHERE d.driverId = (SELECT driverId FROM driver_wins LIMIT 1)
    GROUP BY d.driverId
),
driver_info AS (
    SELECT dw.forename, dw.surname, dw.nationalit
```
- hit=False rows=1 stored_v2=True v2==R0pick=False legacy==stored=False
  legacy=`1_3_1d34187144fdb0e8fd84e7c6047beae1` v2=`b0f21e2936fe48ea76b69bd3839f925d`
```sql
WITH driver_wins AS (
    SELECT d.driverId, d.forename, d.surname, d.nationality, SUM(ds.wins) AS total_wins
    FROM drivers d
    JOIN driverStandings ds ON d.driverId = ds.driverId
    GROUP BY d.driverId, d.forename, d.surname, d.nationality
    ORDER BY total_wins DESC
    LIMIT 1
),
driver_max_points AS (
    SELECT d.driverId, MAX(ds.points) AS max_points
    FROM drivers d
    JOIN driverStandings ds ON d.driverId = ds.driverId
    WHERE d.driverId = (SELECT driverId FROM driver_wins LIMIT 1)
    GROUP BY d.driverId
),
driver_info AS (
    SELECT dw.forename, dw.surname, dw.nationalit
```
