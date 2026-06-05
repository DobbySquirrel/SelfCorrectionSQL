# Diagnostic Summary (Task 6)

Generated: 2026-06-03T13:11:18

## 6.1 Key findings

- **Hash neutrality (30q)**: A0 vs A3 identical Hit@1/Recall (20/30, 25/30); multi-bucket −1.1pp — safe to adopt v2.
- **Recall − Hit@1 gap ~14pp** on 498q (Final 14.1pp, baseline 14.1pp) — stable across configs in our data.
- **498 regressions (84 q)**: 58% recall lost / 42% selection-only (confounded: Coder r=8 v2 vs Qwen r=20 legacy).
- **r=8 → r=20 cost-quality (30q)**: ~2× wall-clock, +1 Hit@1, recall flat — diminishing returns for rollouts alone.
- **No r=2 / 300q data** — cost-quality curve limited to 30q two-point comparison.

## 6.2 Paper data availability matrix

| Paper section | Current data | Gap | Re-run? |
|---|---|---|---|
| §3 Problem formulation | A0/A3 30q + H1 judge | — | No |
| §5.1 Cluster prevalence | Final 498 + expansion stats | — | No |
| §5.2 Cluster homogeneity | H1 r=8/r=20 caches | — | No |
| §5.3 Motivation gap | Multi-r gap table | r=2 point missing | No (optional) |
| §5.4 Cost profile | stats.timing on 30q/498q | No token counts | No |
| §6 Hash ablation | 30q neutral | 498q Coder legacy | **Optional ~8h** |
| §6 Error analysis | 5 hard + 84 regress | — | No |

## 6.3 Decision options

| Option | Action | Time |
|---|---|---|
| **A** | Ship with current data + overnight **Coder r=8 legacy 498** hash ablation | +8h |
| **B** | Ship now; §6 states "30q hash neutral; 498 ablation future work" | 0 |
| **C** | Most valuable single re-run: **Coder r=8 legacy 498** (only clean hash gap); skip r=2 unless reviewer asks | +8h |

**Recommendation**: **Option B** for timeline; Option A if reviewer-proof hash table needed.

## 6.4 Caveats (must disclose in paper)

1. 498 Final vs baseline is **not** isolated ablation (model + rollouts + hash differ).
2. Cost metrics are question-level LLM+DB timing only; no token accounting.
3. 30q rollout curve (r=8 vs r=20) is underpowered (n=30).

4. **Shard hot-spot**: shard(s) [1] show elevated regression — verify multi-GPU setup.
