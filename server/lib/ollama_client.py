"""Local Ollama inference client — the LazusAI model runs on the LazusAI
server (2.28.50.100:11434), no external AI APIs.

Uses Ollama's native /api/chat endpoint so we can control num_ctx for
multi-turn booking conversations. Fallback chain: primary model, then the
tuned booking model. Raises LlmUnavailable only if every model fails.

Model context is injected per-client by the core pipeline's system prompt
(inbound.build_customer_prompt), so the base model is used — the tuned
lazusai-booking model (dental demo persona) is only a fallback.
"""
from __future__ import annotations

import os
import logging
from dataclasses import dataclass
from typing import Iterable

import httpx

log = logging.getLogger("lazusai.ollama")

OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434")
REQUEST_TIMEOUT = float(os.environ.get("OLLAMA_TIMEOUT", "120"))

# Fallback chain. Override via env to match deployed models.
DEFAULT_CHAIN = [
    os.environ.get("OLLAMA_MODEL_PRIMARY", "mistral-nemo:12b"),
    os.environ.get("OLLAMA_MODEL_FALLBACK", "lazusai-booking:latest"),
]

DEFAULT_CTX = int(os.environ.get("OLLAMA_NUM_CTX", "8192"))


class LlmUnavailable(RuntimeError):
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
    num_ctx: int = DEFAULT_CTX,
) -> ChatResult:
    """Run a chat completion — DeepSeek API first when configured, then the
    local Ollama chain. Returns the first successful completion + model."""

    # ---- DeepSeek cloud (fast path) ------------------------------------
    ds_key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if ds_key:
        ds_base = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1").rstrip("/")
        ds_model = os.environ.get("DEEPSEEK_MODEL", "deepseek-chat")
        try:
            with httpx.Client(timeout=60) as client:
                resp = client.post(
                    f"{ds_base}/chat/completions",
                    headers={"Authorization": f"Bearer {ds_key}"},
                    json={
                        "model": ds_model,
                        "messages": messages,
                        "temperature": temperature,
                        "max_tokens": max_tokens,
                        "stream": False,
                    },
                )
                resp.raise_for_status()
                data = resp.json()
                text = (data.get("choices") or [{}])[0].get("message", {}).get("content", "").strip()
                if text:
                    log.info("DeepSeek completion (%s, %ss)", ds_model,
                             round(resp.elapsed.total_seconds(), 2))
                    return ChatResult(text=text, model=f"deepseek:{ds_model}")
                log.warning("DeepSeek returned empty completion, falling back to Ollama")
        except Exception as exc:  # noqa: BLE001 — fall through to local models
            log.warning("DeepSeek failed (%s), falling back to Ollama", exc)

    # ---- Local Ollama chain --------------------------------------------
    models = list(chain) if chain is not None else list(DEFAULT_CHAIN)

    last_error: Exception | None = None
    with httpx.Client(timeout=REQUEST_TIMEOUT) as client:
        for model in models:
            try:
                resp = client.post(
                    f"{OLLAMA_BASE_URL.rstrip('/')}/api/chat",
                    json={
                        "model": model,
                        "messages": messages,
                        "stream": False,
                        "options": {
                            "temperature": temperature,
                            "num_predict": max_tokens,
                            "num_ctx": num_ctx,
                        },
                    },
                )
                resp.raise_for_status()
                data = resp.json()
                text = (data.get("message") or {}).get("content", "").strip()
                if text:
                    log.info("Ollama completion from %s", model)
                    return ChatResult(text=text, model=model)
                log.warning("Ollama model %s returned empty completion, falling back", model)
            except Exception as exc:  # noqa: BLE001 — fall through to next model
                last_error = exc
                log.warning("Ollama model %s failed (%s), falling back", model, exc)

    raise LlmUnavailable(f"All Ollama models failed; last error: {last_error}")
