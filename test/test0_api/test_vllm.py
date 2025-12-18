# test_vllm_openai.py
import os
from typing import Optional
from openai import OpenAI
from datetime import datetime

# ---- 配置 ----
BASE_URL = os.environ.get("VLLM_API_URL", "http://localhost:8009/v1")
API_KEY  = os.environ.get("VLLM_API_KEY", "dummy-key")  # vLLM 不校验，给个占位即可
PREFERRED_MODEL = os.environ.get("VLLM_MODEL")  # 可手动指定；不指定则自动取 /v1/models 的第一个

client = OpenAI(base_url=BASE_URL, api_key=API_KEY)

def pick_model(preferred: Optional[str] = None) -> str:
    """从 /v1/models 里挑一个可用 model id。优先环境变量指定。"""
    models = client.models.list().data
    if not models:
        raise RuntimeError("No models served by vLLM. Check vLLM logs / --served-model-name.")
    ids = sorted([m.id for m in models])
    if preferred and preferred in ids:
        return preferred
    if preferred and preferred not in ids:
        print(f"[warn] preferred model '{preferred}' not in served models: {ids}")
    print(f"[info] served models: {ids}")
    return ids[0]

def chat_once(model: str):
    print("\n=== Chat (non-stream) ===")
    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": "You are a helpful coding assistant."},
            {"role": "user", "content": "Write a Python function to check if a number is prime."},
        ],
        temperature=0.2,
        max_tokens=256,
    )
    print(resp.choices[0].message.content)



def main():
    print(f"[info] base_url={BASE_URL}")
    print(f"[info] time={datetime.now().isoformat(timespec='seconds')}")
    model = pick_model(PREFERRED_MODEL)
    print(f"[info] using model: {model}")

    chat_once(model)


if __name__ == "__main__":
    main()
