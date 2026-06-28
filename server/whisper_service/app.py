"""LazusAI Whisper transcription service (Hetzner VPS, internal, port 8002).

Local openai/whisper inference — no external APIs. Used by n8n Workflow 1 to
transcribe iMessage voice notes (.caf / .m4a) before the LLM step.

Endpoints:
  GET  /health
  POST /transcribe   { "url": "<audio url>" }        — fetch then transcribe
                     { "audio_base64": "...", "filename": "x.m4a" }
                     Optional: "auth" header X-LazusAI-Key, "language": "en"

Returns: { "text": "<transcript>", "language": "en", "duration": 12.3 }

BlueBubbles voice notes are typically .caf/.m4a; ffmpeg (whisper dependency)
handles both. Set WHISPER_MODEL (tiny|base|small|medium|large) via env;
default "base" balances speed/accuracy on CPU.
"""
from __future__ import annotations

import base64
import os
import tempfile
import logging

import httpx
import whisper
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("lazusai.whisper")

MODEL_NAME = os.environ.get("WHISPER_MODEL", "base")
INTERNAL_KEY = os.environ.get("LAZUSAI_WHISPER_KEY", "")  # optional shared secret
# BlueBubbles base + password to resolve relative attachment download URLs.
BLUEBUBBLES_URL = os.environ.get("BLUEBUBBLES_URL", "").rstrip("/")
BLUEBUBBLES_PASSWORD = os.environ.get("BLUEBUBBLES_PASSWORD", "")

app = FastAPI(title="LazusAI Whisper", version="0.1.0")
_model = None


def model():
    global _model
    if _model is None:
        log.info("Loading Whisper model: %s", MODEL_NAME)
        _model = whisper.load_model(MODEL_NAME)
    return _model


class TranscribeRequest(BaseModel):
    url: str | None = None
    audio_base64: str | None = None
    filename: str | None = "audio.m4a"
    language: str | None = None


@app.get("/health")
def health():
    return {"ok": True, "service": "lazusai-whisper", "model": MODEL_NAME}


@app.post("/transcribe")
def transcribe(req: TranscribeRequest, x_lazusai_key: str | None = Header(default=None)):
    if INTERNAL_KEY and x_lazusai_key != INTERNAL_KEY:
        raise HTTPException(status_code=401, detail="bad key")

    audio_bytes = _resolve_audio(req)
    if not audio_bytes:
        raise HTTPException(status_code=400, detail="no audio (provide url or audio_base64)")

    suffix = os.path.splitext(req.filename or "audio.m4a")[1] or ".m4a"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=True) as tmp:
        tmp.write(audio_bytes)
        tmp.flush()
        opts = {}
        if req.language:
            opts["language"] = req.language
        result = model().transcribe(tmp.name, fp16=False, **opts)

    text = (result.get("text") or "").strip()
    log.info("Transcribed %d bytes -> %d chars", len(audio_bytes), len(text))
    return {
        "text": text,
        "language": result.get("language"),
        "duration": _safe_duration(result),
    }


def _resolve_audio(req: TranscribeRequest) -> bytes | None:
    if req.audio_base64:
        return base64.b64decode(req.audio_base64)
    if req.url:
        url = req.url
        params = {}
        # Relative BlueBubbles attachment paths -> absolute + auth password.
        if url.startswith("/") and BLUEBUBBLES_URL:
            url = f"{BLUEBUBBLES_URL}{url}"
            if BLUEBUBBLES_PASSWORD:
                params["password"] = BLUEBUBBLES_PASSWORD
        resp = httpx.get(url, params=params, timeout=60, follow_redirects=True)
        resp.raise_for_status()
        return resp.content
    return None


def _safe_duration(result: dict) -> float | None:
    segs = result.get("segments") or []
    if segs:
        return round(float(segs[-1].get("end", 0)), 2)
    return None
