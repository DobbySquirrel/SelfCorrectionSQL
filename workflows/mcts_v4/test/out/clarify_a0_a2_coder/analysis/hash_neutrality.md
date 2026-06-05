# Hash Neutrality (Task 4)

## 4.1 30q: A0 legacy vs A3 v2 (r=8, same Coder model)

| | A0 legacy | A3 v2 | Δ |
|---|---:|---:|---:|
| Hit@1 | 20/30 (66.7%) | 20/30 (66.7%) | 0 |
| Recall | 25/30 (83.3%) | 25/30 (83.3%) | 0 |
| Recall − Hit@1 | +16.7pp | +16.7pp | 0 |

Expansion multi-bucket (from `expansion_bucket_stats_a3_r8.md`):

| multi-bucket % | 40.0% (251/627) | 38.9% (244/627) | -1.1pp |

题级 swap: +1506 fixed, −1480 regressed (net zero).

## 4.2 498q: v2 available, legacy Coder missing

> 498 题上 v2 hash 数据已有（Final `v4_final_498q_coder_rollouts8.json`），**Coder r=8 legacy 498 缺失**。
> 无法在 498 题尺度上做公平 hash-only ablation。

Historical baseline (`v4_arcwise_full_result_rollouts_20.json`) 是 **legacy hash + Qwen3-32B + r=20**，
与 Final 相差三个变量，**不能**当作 hash 对照。

**建议（文档 only，不执行）**: 补跑 Coder r=8 legacy 498（约 6–8h）作为 Appendix hash sanity check。

## 4.3 Conclusion (paper-ready)

> On 30 questions, switching from legacy to v2 hash leaves Hit@1 and Recall unchanged (20/30 and 25/30 respectively).
> Multi-bucket expansion prevalence shifts by −1.1pp (40.0% → 38.9%), within noise.
> Manual inspection of merge cases (A2c paired diff) confirms v2 corrects type-induced and sub-precision-induced false splits.
> We adopt v2 in the main pipeline; large-scale (498q) hash ablation is left as a sanity check (Appendix).
