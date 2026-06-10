# CT1-v2 / CT2 — CTE Diversity A/B

Generated: 2026-06-08T13:40:19
JSON: `workflows/mcts_v4/test/out/cte_diverse/ct1v2_ct2/cte_diversity_ab_merged.json`

## CT1-v2: diverse instruction vs temperature (5 calls vs 1 call, ~5 candidates)

| qid | A struct | B struct | Δ | A result | B result | Δ | B wins struct |
|---|---:|---:|---:|---:|---:|---:|:---:|
| 1471 | 3 | 5 | +2 | 1 | 5 | +4 | ✓ |
| 1472 | 3 | 5 | +2 | 3 | 3 | +0 | ✓ |
| 1473 | 4 | 5 | +1 | 1 | 4 | +3 | ✓ |
| 1476 | 4 | 5 | +1 | 2 | 5 | +3 | ✓ |
| 1479 | 4 | 5 | +1 | 1 | 5 | +4 | ✓ |

- B wins structure on **5/5** qids
- Mean Δ unique structure (B−A): **+1.40**
- **CT1-v2 gate (structure): PASS** (need B > A on majority qids)

## CT2: diverse×3temp vs standard×12temp (deduped budget ~12)

| qid | A' struct | C struct | Δ | A' result | C result | Δ | C wins |
|---|---:|---:|---:|---:|---:|---:|:---:|
| 1471 | 4 | 10 | +6 | 1 | 8 | +7 | ✓ |
| 1472 | 6 | 10 | +4 | 6 | 7 | +1 | ✓ |
| 1473 | 9 | 10 | +1 | 4 | 8 | +4 | ✓ |
| 1476 | 8 | 9 | +1 | 2 | 8 | +6 | ✓ |
| 1479 | 7 | 13 | +6 | 2 | 12 | +10 | ✓ |

- C wins structure on **5/5** qids
- **CT2 gate (structure): PASS**

## Per-qid candidate audit

### qid=1471 — Identify the total count of customers who pay in EUR
- **A** (standard×5temp): candidates=5 unique_struct=3 unique_result=1 calls=5
  - #1 struct=18e83779… result=1_2_5dd9… valid=True 
  - #2 struct=532feaca… result=1_2_5dd9… valid=True 
  - #3 struct=97b4cbd0… result=1_2_5dd9… valid=True 
  - #4 struct=532feaca… result=1_2_5dd9… valid=True 
  - #5 struct=18e83779… result=1_2_5dd9… valid=True 
- **B** (diverse×1temp): candidates=5 unique_struct=5 unique_result=5 calls=1
  - #1 struct=745ade41… result=1_1_3d8c… valid=True Count customers paying in EUR directly from the customers table using a conditional aggregate with C
  - #2 struct=45d8d9f3… result=5_1_5f2a… valid=True Filter customers by EUR currency first, then count them using a GROUP BY on the filtered data.
  - #3 struct=5dfe71a3… result=5_3_30da… valid=True Use a window function to assign row numbers based on currency, then sum rows where currency equals E
  - #4 struct=61a1ee7b… result=2_2_a7c3… valid=True Aggregate all customers by currency and then extract the count for EUR using a pivot-style approach.
  - #5 struct=1b7b8607… result=1_1_7f00… valid=True Join customers with a dummy table containing only EUR values to isolate EUR-paying customers before 
- **C** (diverse×3temp): candidates=10 unique_struct=10 unique_result=8 calls=3
  - #1 struct=745ade41… result=1_1_3d8c… valid=True 
  - #2 struct=45d8d9f3… result=5_1_5f2a… valid=True 
  - #3 struct=5dfe71a3… result=5_3_30da… valid=True 
  - #4 struct=b9a28a60… result=2_2_a7c3… valid=True 
  - #5 struct=4368501d… result=1_2_5dd9… valid=True 
  - #6 struct=568727a8… result=5_2_df41… valid=True 
  - #7 struct=6c035a9b… result=5_3_6f35… valid=True 
  - #8 struct=a1393c6d… result=5_1_e577… valid=True 
  - #9 struct=61a1ee7b… result=2_2_a7c3… valid=True 
  - #10 struct=78ce2a17… result=5_1_5f2a… valid=True 
- **A_prime** (standard×12temp): candidates=12 unique_struct=4 unique_result=1 calls=12
  - #1 struct=e163852e… result=1_2_5dd9… valid=True 
  - #2 struct=532feaca… result=1_2_5dd9… valid=True 
  - #3 struct=97b4cbd0… result=1_2_5dd9… valid=True 
  - #4 struct=532feaca… result=1_2_5dd9… valid=True 
  - #5 struct=18e83779… result=1_2_5dd9… valid=True 
  - #6 struct=532feaca… result=1_2_5dd9… valid=True 
  - #7 struct=532feaca… result=1_2_5dd9… valid=True 
  - #8 struct=532feaca… result=1_2_5dd9… valid=True 
  - #9 struct=532feaca… result=1_2_5dd9… valid=True 
  - #10 struct=532feaca… result=1_2_5dd9… valid=True 
  - #11 struct=18e83779… result=1_2_5dd9… valid=True 
  - #12 struct=97b4cbd0… result=1_2_5dd9… valid=True 

### qid=1472 — Identify customers in the LAM segment
- **A** (standard×5temp): candidates=5 unique_struct=3 unique_result=3 calls=5
  - #1 struct=640a81a0… result=1_2_36c7… valid=True 
  - #2 struct=2e95c5f7… result=5_2_cddc… valid=True 
  - #3 struct=2e95c5f7… result=5_2_cddc… valid=True 
  - #4 struct=2e95c5f7… result=5_2_cddc… valid=True 
  - #5 struct=ba3506ee… result=5_1_3225… valid=True 
- **B** (diverse×1temp): candidates=5 unique_struct=5 unique_result=3 calls=1
  - #1 struct=c7fa9788… result=5_1_3225… valid=True Filter customers by Segment = 'LAM' directly from the customers table to identify all LAM segment cu
  - #2 struct=d5c125b8… result=5_1_cb6d… valid=True Join customers table with yearmonth table on CustomerID and filter for LAM segment, then group by Cu
  - #3 struct=b19a23ba… result=5_1_3225… valid=True Use a subquery to extract distinct CustomerIDs from customers where Segment is 'LAM', then wrap in a
  - #4 struct=755c94ab… result=5_1_c8aa… valid=True Aggregate data from yearmonth table to find customers with consumption records in 2012, then join wi
  - #5 struct=ca8df97a… result=5_1_c8aa… valid=True First identify all unique customers in 2012 by joining yearmonth with customers, then filter only th
- **C** (diverse×3temp): candidates=10 unique_struct=10 unique_result=7 calls=3
  - #1 struct=c7fa9788… result=5_1_3225… valid=True 
  - #2 struct=b44d5a84… result=5_1_c8aa… valid=True 
  - #3 struct=0d8a2eee… result=5_2_d62e… valid=True 
  - #4 struct=957da69d… result=5_3_b131… valid=True 
  - #5 struct=d9ac612e… result=5_1_f9d0… valid=True 
  - #6 struct=0109b76b… result=5_1_c8aa… valid=True 
  - #7 struct=358df5f9… result=5_2_69c2… valid=True 
  - #8 struct=16483a90… result=5_1_f9d0… valid=True 
  - #9 struct=f487b9d2… result=5_1_9d91… valid=True 
  - #10 struct=4fd58b97… result=5_1_f9d0… valid=True 
- **A_prime** (standard×12temp): candidates=11 unique_struct=6 unique_result=6 calls=12
  - #1 struct=2e95c5f7… result=5_2_cddc… valid=True 
  - #2 struct=ba89aa81… result=1_2_bc6c… valid=True 
  - #3 struct=640a81a0… result=1_2_36c7… valid=True 
  - #4 struct=2e95c5f7… result=5_2_cddc… valid=True 
  - #5 struct=2e95c5f7… result=5_2_cddc… valid=True 
  - #6 struct=ba3506ee… result=5_1_3225… valid=True 
  - #7 struct=97247a5b… result=5_3_c06d… valid=True 
  - #8 struct=2e95c5f7… result=5_2_cddc… valid=True 
  - #9 struct=2e95c5f7… result=5_2_cddc… valid=True 
  - #10 struct=f3cbcbdb… result=5_2_3fe8… valid=True 
  - #11 struct=ba3506ee… result=5_1_3225… valid=True 

### qid=1473 — Identify customers in the SME segment
- **A** (standard×5temp): candidates=5 unique_struct=4 unique_result=1 calls=5
  - #1 struct=619a3242… result=1_1_177f… valid=True 
  - #2 struct=5fdd19e3… result=None… valid=False 
  - #3 struct=696cb8f0… result=None… valid=False 
  - #4 struct=9ecfa1cb… result=None… valid=False 
  - #5 struct=696cb8f0… result=None… valid=False 
- **B** (diverse×1temp): candidates=5 unique_struct=5 unique_result=4 calls=1
  - #1 struct=c7fa9788… result=5_1_e3ba… valid=True Filter customers by SME segment directly from the customers table and select distinct CustomerIDs to
  - #2 struct=8e44cf1e… result=5_1_a9fa… valid=True Join customers table with transactions_1k to find customers who made transactions and filter those i
  - #3 struct=6418cdbf… result=5_1_e3ba… valid=True Use a subquery to first extract all CustomerIDs from the customers table where Segment is 'SME', the
  - #4 struct=0109b76b… result=5_1_8e62… valid=True Filter customers by Segment='SME' and join with yearmonth table to confirm these customers have cons
  - #5 struct=2b2e0cb1… result=3_2_99a5… valid=True Aggregate customer data by segment first, then filter for SME segment, which allows us to verify tha
- **C** (diverse×3temp): candidates=10 unique_struct=10 unique_result=8 calls=3
  - #1 struct=c7fa9788… result=5_1_e3ba… valid=True 
  - #2 struct=d5c125b8… result=5_1_e5d5… valid=True 
  - #3 struct=b24742f9… result=5_3_eaf3… valid=True 
  - #4 struct=e4d54e50… result=5_1_8e62… valid=True 
  - #5 struct=8e44cf1e… result=5_1_a9fa… valid=True 
  - #6 struct=c82012e6… result=5_2_e989… valid=True 
  - #7 struct=33e079c7… result=5_1_8e62… valid=True 
  - #8 struct=a288431f… result=5_1_8e62… valid=True 
  - #9 struct=0d8a2eee… result=5_2_8c3b… valid=True 
  - #10 struct=79fba7ca… result=5_1_140e… valid=True 
- **A_prime** (standard×12temp): candidates=11 unique_struct=9 unique_result=4 calls=12
  - #1 struct=619a3242… result=1_1_177f… valid=True 
  - #2 struct=696cb8f0… result=None… valid=False 
  - #3 struct=ba3506ee… result=5_1_e3ba… valid=True 
  - #4 struct=ef451909… result=None… valid=False 
  - #5 struct=fe2250f9… result=None… valid=False 
  - #6 struct=d34662c2… result=5_2_ac7d… valid=True 
  - #7 struct=ba3506ee… result=5_1_e3ba… valid=True 
  - #8 struct=c3bae7fd… result=1_1_3e73… valid=True 
  - #9 struct=2e97a148… result=1_1_3e73… valid=True 
  - #10 struct=696cb8f0… result=None… valid=False 
  - #11 struct=1e3069b5… result=None… valid=False 

### qid=1476 — Identify all customers who paid in CZK and their total gas consumption in 2012
- **A** (standard×5temp): candidates=4 unique_struct=4 unique_result=2 calls=5
  - #1 struct=51dbab45… result=1_1_fdcb… valid=True 
  - #2 struct=fc0eb27e… result=1_1_fdcb… valid=True 
  - #3 struct=f0a2f7d4… result=2_2_1e20… valid=True 
  - #4 struct=0ed7260a… result=1_1_fdcb… valid=True 
- **B** (diverse×1temp): candidates=5 unique_struct=5 unique_result=5 calls=1
  - #1 struct=45d8d9f3… result=5_1_564c… valid=True Filter customers by CZK currency first, then join with yearmonth table to sum up their gas consumpti
  - #2 struct=b9d99133… result=5_3_3934… valid=True Aggregate gas consumption by customer and year first, then filter for CZK paying customers in 2012.
  - #3 struct=a1393c6d… result=5_1_37bb… valid=True Join transactions with customers to identify CZK paying customers, then aggregate their gas consumpt
  - #4 struct=0d8a2eee… result=5_2_d62e… valid=True Start with yearmonth data filtered for 2012, then join with customers to select only CZK-paying cust
  - #5 struct=690d2655… result=5_3_d675… valid=True Use a window function approach to calculate total consumption per customer, then filter for CZK cust
- **C** (diverse×3temp): candidates=9 unique_struct=9 unique_result=8 calls=3
  - #1 struct=45d8d9f3… result=5_1_564c… valid=True 
  - #2 struct=0d8a2eee… result=5_2_d62e… valid=True 
  - #3 struct=63ef76f6… result=5_2_5331… valid=True 
  - #4 struct=493acf00… result=5_2_5a5d… valid=True 
  - #5 struct=4c69d7e9… result=5_2_0058… valid=True 
  - #6 struct=71905b0b… result=5_2_cee8… valid=True 
  - #7 struct=4d1c0c8d… result=5_3_3934… valid=True 
  - #8 struct=c4ba1e53… result=5_2_d62e… valid=True 
  - #9 struct=a1393c6d… result=5_1_37bb… valid=True 
- **A_prime** (standard×12temp): candidates=11 unique_struct=8 unique_result=2 calls=12
  - #1 struct=f0a2f7d4… result=2_2_1e20… valid=True 
  - #2 struct=4b630a21… result=2_2_1e20… valid=True 
  - #3 struct=e737d2a6… result=2_2_1e20… valid=True 
  - #4 struct=fc0eb27e… result=1_1_fdcb… valid=True 
  - #5 struct=f0a2f7d4… result=2_2_1e20… valid=True 
  - #6 struct=993db7ee… result=1_1_fdcb… valid=True 
  - #7 struct=02515193… result=2_2_1e20… valid=True 
  - #8 struct=4b630a21… result=2_2_1e20… valid=True 
  - #9 struct=6e356bab… result=1_1_fdcb… valid=True 
  - #10 struct=83519b24… result=2_2_1e20… valid=True 
  - #11 struct=f0a2f7d4… result=2_2_1e20… valid=True 

### qid=1479 — Identify the relevant tables and their relationships to determine gas consumption paid in CZK.
- **A** (standard×5temp): candidates=5 unique_struct=4 unique_result=1 calls=5
  - #1 struct=2ef15061… result=1_2_0990… valid=True 
  - #2 struct=2ef15061… result=1_2_0990… valid=True 
  - #3 struct=257c6543… result=1_2_0990… valid=True 
  - #4 struct=65809313… result=1_2_0990… valid=True 
  - #5 struct=de0d3460… result=1_2_0990… valid=True 
- **B** (diverse×1temp): candidates=5 unique_struct=5 unique_result=5 calls=1
  - #1 struct=18f99d07… result=5_5_6c6b… valid=True To identify gas consumption paid in CZK, we need to join transactions with customers to filter by Cu
  - #2 struct=45d8d9f3… result=5_1_564c… valid=True We begin by filtering customers who pay in CZK, then link those to their transaction records and agg
  - #3 struct=0a211ca0… result=5_4_e1d1… valid=True This approach starts by joining transactions and yearmonth tables directly, then filters for CZK pay
  - #4 struct=c606552a… result=5_3_f347… valid=True We create a combined dataset of all transactions and their associated consumption data, then filter 
  - #5 struct=6be01e16… result=5_3_0819… valid=True Here we first extract the yearly consumption data from yearmonth, then cross-reference it with trans
- **C** (diverse×3temp): candidates=13 unique_struct=13 unique_result=12 calls=3
  - #1 struct=18f99d07… result=5_5_6c6b… valid=True 
  - #2 struct=0ef3196a… result=5_2_a8ec… valid=True 
  - #3 struct=4c24c7ce… result=5_3_879e… valid=True 
  - #4 struct=c447dbeb… result=3_1_27b6… valid=True 
  - #5 struct=a1393c6d… result=5_1_37bb… valid=True 
  - #6 struct=c7ece522… result=5_3_709e… valid=True 
  - #7 struct=45d8d9f3… result=5_1_564c… valid=True 
  - #8 struct=1cc1afac… result=5_2_3e95… valid=True 
  - #9 struct=885e12b1… result=5_3_c91b… valid=True 
  - #10 struct=af854a66… result=5_2_f2fa… valid=True 
  - #11 struct=1c70f517… result=1_2_8b08… valid=True 
  - #12 struct=77d1dc76… result=5_1_564c… valid=True 
  - #13 struct=5cff150e… result=5_2_1355… valid=True 
- **A_prime** (standard×12temp): candidates=12 unique_struct=7 unique_result=2 calls=12
  - #1 struct=257c6543… result=1_2_0990… valid=True 
  - #2 struct=66a38979… result=1_2_0990… valid=True 
  - #3 struct=65809313… result=1_2_0990… valid=True 
  - #4 struct=6a032b14… result=3_2_016f… valid=True 
  - #5 struct=65809313… result=1_2_0990… valid=True 
  - #6 struct=257c6543… result=1_2_0990… valid=True 
  - #7 struct=257c6543… result=1_2_0990… valid=True 
  - #8 struct=d0eaf879… result=1_2_0990… valid=True 
  - #9 struct=de0d3460… result=1_2_0990… valid=True 
  - #10 struct=2ef15061… result=1_2_0990… valid=True 
  - #11 struct=257c6543… result=1_2_0990… valid=True 
  - #12 struct=66a38979… result=1_2_0990… valid=True 
