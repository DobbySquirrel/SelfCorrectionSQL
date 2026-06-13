"""Thread-local OpenAI client pool with HTTP keep-alive."""

from __future__ import annotations

import threading
from typing import Dict, Tuple

import httpx
from openai import OpenAI

_thread_local = threading.local()
_HTTP_LIMITS = httpx.Limits(max_connections=100, max_keepalive_connections=20)


def get_openai_client(
    base_url: str,
    api_key: str = "dummy-key",
    timeout: float = 120.0,
) -> OpenAI:
    """Return a thread-local cached OpenAI client (safe for ThreadPoolExecutor workers)."""
    clients: Dict[Tuple[str, str, float], OpenAI] = getattr(_thread_local, "clients", None)
    if clients is None:
        clients = {}
        _thread_local.clients = clients

    key = (base_url, api_key, timeout)
    if key not in clients:
        http_client = httpx.Client(limits=_HTTP_LIMITS)
        clients[key] = OpenAI(
            base_url=base_url,
            api_key=api_key,
            timeout=timeout,
            http_client=http_client,
        )
    return clients[key]
