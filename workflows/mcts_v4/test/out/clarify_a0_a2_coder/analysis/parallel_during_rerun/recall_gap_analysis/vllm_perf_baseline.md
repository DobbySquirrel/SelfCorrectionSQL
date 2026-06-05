# vLLM Prefix Cache Baseline (R3)

Generated: 2026-06-03

**纯读摸底** — 不重启 vLLM。

## 1. 当前启动命令（脱敏）

`scripts/start_vllm_qwencoder30b_4gpu.sh`（4 分片 screen）:

```bash
vllm serve ${MODEL_PATH} \
  --dtype auto --host 0.0.0.0 --port ${PORT} \
  --gpu-memory-utilization 0.9 \
  --enable-prefix-caching \
  --enable-chunked-prefill \
  --tensor-parallel-size 2
```

## 2. Prefix cache / chunked prefill

| 参数 | 状态 |
|---|---|
| `--enable-prefix-caching` | ✅ 已写入 `start_vllm_qwencoder30b_4gpu.sh`（需重启生效） |
| `--enable-chunked-prefill` | ✅ 已写入 `start_vllm_qwencoder30b_4gpu.sh`（需重启生效） |

`perf_baseline.md` 实测 5× 相同 prompt：`cached_tokens` 均为 **None/0**。

## 3. 已落地改动（需重启 vLLM 后生效）

已在 `scripts/start_vllm_qwencoder30b_4gpu.sh` 加入 `--enable-prefix-caching` 与 `--enable-chunked-prefill`。

- 预期：CTE 阶段 **~10–25%** wall（DDL 重复）
- 风险：低
- **验证**：避开 MCTS 高峰；先单分片重启，同一 prompt 连打 5 次看 `cached_tokens > 0`

## 4. 其他可选

- `--max-num-seqs` / `--max-num-batched-tokens` 按显存调
- 代码侧：shared OpenAI client（与 prefix cache 正交，见 `perf_baseline.md`）

## 5. 单分片验证（重启后）

只动 **shard0（:8000）** 时：先 `screen -S vllm_coder30b_shard0 -X quit`，再按脚本只起该分片，或全量 `bash scripts/start_vllm_qwencoder30b_4gpu.sh`。

### 5.1 确认进程带新参数

```bash
ps aux | grep "[v]llm serve" | grep -E "prefix-caching|chunked-prefill" | head -1
```

### 5.2 连打 5 次（同一前缀，看 cached_tokens）

```bash
PORT=8000 python3 - <<'PY'
import json, urllib.request

port = __import__("os").environ.get("PORT", "8000")
base = f"http://127.0.0.1:{port}"
model = json.load(urllib.request.urlopen(f"{base}/v1/models"))["data"][0]["id"]
prefix = (
    "You are a SQL expert. Schema DDL:\n"
    "CREATE TABLE t1 (id INT, name TEXT);\n"
    "CREATE TABLE t2 (id INT, val REAL);\n"
    + "x" * 2000
)
body = lambda i: json.dumps({
    "model": model,
    "messages": [
        {"role": "system", "content": prefix},
        {"role": "user", "content": f"call {i}: say OK"},
    ],
    "max_tokens": 8,
    "temperature": 0,
}).encode()

print("| call | prompt_tokens | cached_tokens | completion_tokens |")
print("|---|---:|---:|---:|")
for i in range(1, 6):
    req = urllib.request.Request(
        f"{base}/v1/chat/completions",
        data=body(i),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    u = json.load(urllib.request.urlopen(req))["usage"]
    cached = (u.get("prompt_tokens_details") or {}).get("cached_tokens", u.get("cached_tokens"))
    print(f"| {i} | {u.get('prompt_tokens')} | {cached} | {u.get('completion_tokens')} |")
PY
```

**期望**：第 1 次 `cached=0` 或 `None`；第 2–5 次 `cached > 0`。

若 5 次仍全为 `None`/0：检查 §5.1 进程参数、vLLM 版本是否支持 `prompt_tokens_details.cached_tokens`，或把 `prefix` 再加长后重试。
