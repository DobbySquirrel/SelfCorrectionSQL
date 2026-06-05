# Perf Baseline (Step 3)

Generated: 2026-06-03T14:21:33

## 1. HTTP client 现状

- SDK: **OpenAI Python SDK** (`from openai import OpenAI`)
- `cte_generator.py`: **per-call / per-thread 新建** `OpenAI(...)` inside `generate_group()`
- `complete_sql_generator.py`: 同样模式
- **无** 模块级单例、**无** 显式 `httpx` connection pool / keep-alive 配置

```python
# cte_generator.py:1393
                
                # 每个线程创建独立的client（OpenAI client不是线程安全的）
                # 设置超时为120秒，避免LLM调用卡住
                client = OpenAI(base_url=selected_base_url, api_key=selected_api_key, timeout=120.0)
                start_time = time.time()
                response = client.chat.completions.create(
                    model=selected_model,
```

## 2. vLLM 进程参数

```text
sshen190   33258  7.2  0.1 12768168 1147272 pts/4 Sl+ 13:29   3:44 /hpc2hdd/home/sshen190/miniconda3/envs/Qwen3-32B/bin/python3.10 /hpc2hdd/home/sshen190/miniconda3/envs/Qwen3-32B/bin/vllm serve /hpc2hdd/home/sshen190/wtao565/models/Qwen3-Coder-30B --dtype auto --host 0.0.0.0 --port 8000 --gpu-memory-utilization 0.9 --tensor-parallel-size 2
```

- `--enable-prefix-caching`: **NOT in cmdline (likely OFF)**
- `--max-num-seqs` / batched tokens: not set in cmdline (defaults)

## 3. Prefix cache sanity (5× same prompt)

| call | prompt_tokens | cached_tokens | completion_tokens |
|---|---:|---:|---:|
| 1 | 27 | None | 2 |
| 2 | 27 | None | 2 |
| 3 | 27 | None | 2 |
| 4 | 27 | None | 2 |
| 5 | 27 | None | 2 |

**Interpretation**: `cached_tokens` always 0 → prefix caching **not active** on this server.

## 4. Per-call CTE time (final_498 ex-ef2)

| n | mean cte share | P50/call | P95/call | max/call |
|---:|---:|---:|---:|---:|
| 447 | 85% | 0.77s | 1.30s | 1.89s |

### Calls >5s (qid, per-call est, cte_gen_s)


## 5. Quick wins (ranked)

| Rank | Item | Status | Est. gain | LOC |
|---:|---|---|---:|---:|
| 1 | Shared `OpenAI` client + httpx keep-alive in CTE/SQL generators | **Not done** | 5–15% cte | ~30–50 |
| 2 | vLLM `--enable-prefix-caching` | **Likely off** | 10–25% on repeat DDL | restart |
| 3 | DDL soft trim (value stats) | Not done | 10–20% large DDL | med |
| 4 | Reuse schema string object per question | Not done | 3–8% | low |

**Already OK**: vLLM up on :8000; tensor parallel=2.

## 6. Do NOT touch (semantic)

- Leaf parallelization / virtual loss
- Reward formula / hash v2→v3 without A/B
- Selector rules (Step 1 first)

