"""Per-client ChromaDB access. One collection per tenant: client_<client_id>.

Stores three logical document kinds in each collection, tagged by metadata
`kind`: "context" (business docs), "turn" (conversation turns), "lead"
(lead records). This keeps tenant data fully isolated while allowing typed
queries within a tenant.
"""
from __future__ import annotations

import os
import time
import uuid
from typing import Any

import chromadb

CHROMA_HOST = os.environ.get("CHROMA_HOST", "127.0.0.1")
CHROMA_PORT = int(os.environ.get("CHROMA_PORT", "8001"))

_client: chromadb.api.ClientAPI | None = None


def _conn() -> chromadb.api.ClientAPI:
    global _client
    if _client is None:
        _client = chromadb.HttpClient(host=CHROMA_HOST, port=CHROMA_PORT)
    return _client


def collection_name(client_id: str) -> str:
    return f"client_{client_id}"


def get_collection(client_id: str):
    """Get or create the tenant's collection (idempotent)."""
    return _conn().get_or_create_collection(
        name=collection_name(client_id),
        metadata={"client_id": client_id, "hnsw:space": "cosine"},
    )


def add_context(client_id: str, doc_id: str, text: str, meta: dict | None = None) -> None:
    col = get_collection(client_id)
    md = {"kind": "context", "client_id": client_id, **(meta or {})}
    col.upsert(ids=[f"context:{doc_id}"], documents=[text], metadatas=[md])


def log_turn(client_id: str, role: str, text: str, sender: str = "") -> str:
    """Persist one conversation turn. Returns the stored id."""
    col = get_collection(client_id)
    turn_id = f"turn:{int(time.time()*1000)}:{uuid.uuid4().hex[:8]}"
    col.add(
        ids=[turn_id],
        documents=[text],
        metadatas=[{
            "kind": "turn",
            "client_id": client_id,
            "role": role,
            "sender": sender,
            "ts": time.time(),
        }],
    )
    return turn_id


def query_context(client_id: str, query: str, n: int = 5) -> list[str]:
    """Semantic search over business context documents for this tenant."""
    col = get_collection(client_id)
    res = col.query(
        query_texts=[query],
        n_results=n,
        where={"kind": "context"},
    )
    docs = res.get("documents") or [[]]
    return docs[0] if docs else []


def recent_turns(client_id: str, limit: int = 10) -> list[dict[str, Any]]:
    """Return the most recent conversation turns, oldest-first."""
    col = get_collection(client_id)
    res = col.get(where={"kind": "turn"}, include=["documents", "metadatas"])
    rows = []
    for doc, md in zip(res.get("documents", []), res.get("metadatas", [])):
        rows.append({"text": doc, **(md or {})})
    rows.sort(key=lambda r: r.get("ts", 0))
    return rows[-limit:]


def turns_since(client_id: str, since_ts: float) -> list[dict[str, Any]]:
    """All turns with ts >= since_ts (used by the daily summary)."""
    col = get_collection(client_id)
    res = col.get(where={"kind": "turn"}, include=["documents", "metadatas"])
    rows = []
    for doc, md in zip(res.get("documents", []), res.get("metadatas", [])):
        if (md or {}).get("ts", 0) >= since_ts:
            rows.append({"text": doc, **(md or {})})
    rows.sort(key=lambda r: r.get("ts", 0))
    return rows


def add_lead(client_id: str, lead: dict) -> str:
    col = get_collection(client_id)
    lead_id = f"lead:{int(time.time()*1000)}:{uuid.uuid4().hex[:8]}"
    summary = lead.get("summary") or lead.get("message") or ""
    col.add(
        ids=[lead_id],
        documents=[summary],
        metadatas=[{"kind": "lead", "client_id": client_id, "ts": time.time(), **lead}],
    )
    return lead_id


def reindex_context(client_id: str, documents: list[dict]) -> int:
    """Drop and rebuild the context docs for a tenant from a list of
    {id, text, meta?}. Conversation turns and leads are preserved.
    Returns the number of context docs indexed.
    """
    col = get_collection(client_id)
    existing = col.get(where={"kind": "context"})
    if existing.get("ids"):
        col.delete(ids=existing["ids"])
    if not documents:
        return 0
    col.upsert(
        ids=[f"context:{d['id']}" for d in documents],
        documents=[d["text"] for d in documents],
        metadatas=[{"kind": "context", "client_id": client_id, **(d.get("meta") or {})} for d in documents],
    )
    return len(documents)
