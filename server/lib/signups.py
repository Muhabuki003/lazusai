"""Pending self-serve signups awaiting operator approval.

The /get-started wizard on lazusai.com posts here (via the Pages worker)
instead of creating a tenant directly. An operator reviews the queue (in the
/admin operator dashboard) and approves or rejects; approval provisions the
tenant using the wizard's collected data, so no manual data entry is needed
per client — only assigning a phone number/BlueBubbles chat guid remains
manual, since that depends on real Mac/Apple ID capacity.

File layout (relative to LAZUSAI_DATA_DIR):
  signups/<id>.json   one file per signup
"""
from __future__ import annotations

import json
import time
import uuid
from pathlib import Path

from . import clients

SIGNUPS_DIR = clients.DATA_DIR / "signups"


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _path(signup_id: str) -> Path:
    return SIGNUPS_DIR / f"{signup_id}.json"


def create(fields: dict) -> dict:
    """Store a new pending signup. Returns the saved record."""
    SIGNUPS_DIR.mkdir(parents=True, exist_ok=True)
    signup_id = f"su_{int(time.time())}_{uuid.uuid4().hex[:8]}"
    record = {
        "id": signup_id,
        "status": "pending",  # pending | approved | rejected
        "created_at": _now(),
        "name": fields.get("name", ""),
        "email": fields.get("email", ""),
        "phone": fields.get("phone", ""),
        "business": fields.get("business", ""),
        "industry": fields.get("industry", ""),
        "services": fields.get("services", ""),
        "hours": fields.get("hours", ""),
        "faqs": fields.get("faqs", ""),
        "plan": fields.get("plan", ""),
        "paid": False,
        "stripe_customer_id": "",
        "stripe_subscription_id": "",
        "client_id": "",
    }
    _path(signup_id).write_text(json.dumps(record, indent=2))
    return record


def load(signup_id: str) -> dict | None:
    p = _path(signup_id)
    if not p.exists():
        return None
    return json.loads(p.read_text())


def save(record: dict) -> None:
    SIGNUPS_DIR.mkdir(parents=True, exist_ok=True)
    _path(record["id"]).write_text(json.dumps(record, indent=2))


def list_signups(status: str | None = None) -> list[dict]:
    if not SIGNUPS_DIR.exists():
        return []
    out = []
    for p in sorted(SIGNUPS_DIR.glob("*.json"), reverse=True):
        rec = json.loads(p.read_text())
        if status and rec.get("status") != status:
            continue
        out.append(rec)
    return out


def mark_paid(signup_id: str, stripe_customer_id: str, stripe_subscription_id: str) -> dict | None:
    rec = load(signup_id)
    if not rec:
        return None
    rec["paid"] = True
    rec["stripe_customer_id"] = stripe_customer_id
    rec["stripe_subscription_id"] = stripe_subscription_id
    save(rec)
    return rec


def set_status(signup_id: str, status: str, client_id: str = "") -> dict | None:
    rec = load(signup_id)
    if not rec:
        return None
    rec["status"] = status
    if client_id:
        rec["client_id"] = client_id
    save(rec)
    return rec
