"""NIM inference client with DeepSeek -> Kimi -> GLM 5.2 -> Mistral fallback.

All inference routes through the local NVIDIA NIM stack on the Hetzner VPS
(OpenAI-compatible /v1/chat/completions on port 8000). No external AI APIs.

The fallback chain tries each model in order; if a model errors, times out, or
returns an empty completion, it moves to the next. Raises NimUnavailable only
if every model in the chain fails.
"""
from __future__ import annotations

import os
import logging
from dataclasses import dataclass
from typing import Iterable

import httpx

log = logging.getLogger("lazusai.nim")

NIM_BASE_URL = os.environ.get("NIM_BASE_URL", "http://127.0.0.1:8000")
NIM_API_KEY = os.environ.get("NIM_API_KEY", "")  # local NIM usually keyless
REQUEST_TIMEOUT = float(os.environ.get("NIM_TIMEOUT", "60"))

# Fallback chain. Override model ids via env to match deployed NIM containers.
DEFAULT_CHAIN = [
    os.environ.get("NIM_MODEL_DEEPSEEK", "deepseek-ai/deepseek-r1"),
    os.environ.get("NIM_MODEL_KIMI", "moonshotai/kimi-k2"),
    os.environ.get("NIM_MODEL_GLM", "zhipuai/glm-5.2"),
    os.environ.get("NIM_MODEL_MISTRAL", "mistralai/mistral-large"),
]


class NimUnavailable(RuntimeError):
    """Raised when every model in the fallback chain failed."""


@dataclass
class ChatResult:
    text: str
    model: str


def chat(
    messages: list[dict],
    *,
    chain: Iterable[str] | None = None,
    temperature: float = 0.4,
    max_tokens: int = 700,
) -> ChatResult:
    """Run a chat completion through the fallback chain.

    `messages` is a standard OpenAI-style list of {role, content} dicts.
    Returns the first successful completion and which model produced it.
    """
    models = list(chain) if chain is not None else list(DEFAULT_CHAIN)
    headers = {"Content-Type": "application/json"}
    if NIM_API_KEY:
        headers["Authorization"] = f"Bearer {NIM_API_KEY}"

    last_error: Exception | None = None
    with httpx.Client(timeout=REQUEST_TIMEOUT) as client:
        for model in models:
            try:
                resp = client.post(
                    f"{NIM_BASE_URL.rstrip('/')}/v1/chat/completions",
                    headers=headers,
                    json={
                        "model": model,
                        "messages": messages,
                        "temperature": temperature,
                        "max_tokens": max_tokens,
                    },
                )
                resp.raise_for_status()
                data = resp.json()
                text = (
                    data.get("choices", [{}])[0]
                    .get("message", {})
                    .get("content", "")
                    .strip()
                )
                if text:
                    log.info("NIM completion from %s", model)
                    return ChatResult(text=text, model=model)
                log.warning("NIM model %s returned empty completion, falling back", model)
            except Exception as exc:  # noqa: BLE001 — fall through to next model
                last_error = exc
                log.warning("NIM model %s failed (%s), falling back", model, exc)

    raise NimUnavailable(f"All NIM models failed; last error: {last_error}")
