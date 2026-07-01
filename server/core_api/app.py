"""LazusAI Core API (Hetzner VPS, internal, port 8003).

Single source of truth for client config, leads, and ChromaDB-backed data.
Consumed by:
  - the Cloudflare Worker  (/api/* dashboard proxy, via the tunnel)
  - n8n workflows          (config load, context query, turn/lead logging,
                            daily summary)
  - the Hermes tool        (create/list/pause clients)

All AI summarization routes through the local NIM stack. No external APIs.

Auth: every request must send header X-LazusAI-Key matching LAZUSAI_CORE_KEY
(unless LAZUSAI_CORE_KEY is unset, e.g. in local dev).
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel

# Make the shared libs importable whether run from repo root or server/.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lib import bookings, cf_kv, chroma_store, clients, nim_client, notify, payments  # noqa: E402

CORE_KEY = os.environ.get("LAZUSAI_CORE_KEY", "")

app = FastAPI(title="LazusAI Core API", version="0.1.0")


def _auth(key: str | None):
    if CORE_KEY and key != CORE_KEY:
        raise HTTPException(status_code=401, detail="bad key")


# --------------------------------------------------------------------- models
class ConfigUpdate(BaseModel):
    business_name: str | None = None
    industry: str | None = None
    hours: dict | None = None
    services: list[str] | None = None
    pricing: dict | None = None
    faqs: list[dict] | None = None
    escalation_keywords: list[str] | None = None
    ai_personality: str | None = None
    booking_enabled: bool | None = None
    slot_minutes: int | None = None
    services_matrix: list[dict] | None = None
    staff: list[dict] | None = None
    integrations: dict | None = None


class NewClient(BaseModel):
    business_name: str
    apple_id_number: str
    industry: str = ""
    owner_telegram: str = ""
    bluebubbles_chat_guid: str = ""
    hours: dict = {}
    services: list[str] = []
    pricing: dict = {}
    faqs: list[dict] = []
    escalation_keywords: list[str] = []
    ai_personality: str = ""
    booking_enabled: bool = False
    slot_minutes: int = 30
    services_matrix: list[dict] = []
    staff: list[dict] = []
    integrations: dict = {}


class NewBooking(BaseModel):
    service: str
    date: str            # YYYY-MM-DD
    start: str           # HH:MM (24h)
    staff: str = ""
    customer_name: str = ""
    customer_phone: str = ""
    customer_email: str = ""
    address: str = ""
    notes: str = ""
    source: str = "bot"  # bot | dashboard | web
    # Optional overrides; normally resolved from services_matrix.
    service_price: float | None = None
    service_duration_min: int | None = None
    notify: bool = True  # send staff/owner alert on create


class BookingPatch(BaseModel):
    service: str | None = None
    staff: str | None = None
    date: str | None = None
    start: str | None = None
    service_duration_min: int | None = None
    service_price: float | None = None
    customer_name: str | None = None
    customer_phone: str | None = None
    customer_email: str | None = None
    address: str | None = None
    notes: str | None = None
    status: str | None = None
    payment_status: str | None = None


class Lead(BaseModel):
    sender: str = ""
    name: str = ""
    phone: str = ""
    email: str = ""
    summary: str = ""
    message: str = ""
    ai_response: str = ""
    escalated: bool = False


class Turn(BaseModel):
    role: str
    text: str
    sender: str = ""


# --------------------------------------------------------------------- routes
@app.get("/health")
def health():
    return {"ok": True, "service": "lazusai-core"}


@app.get("/clients")
def list_all(x_lazusai_key: str | None = Header(default=None)):
    _auth(x_lazusai_key)
    out = []
    for cfg in clients.list_clients():
        cid = cfg["client_id"]
        out.append({
            "client_id": cid,
            "business_name": cfg.get("business_name"),
            "active": cfg.get("active", True),
            "messages_today": _messages_today(cid),
            "leads_today": len(clients.todays_leads(cid)),
        })
    return {"clients": out}


@app.post("/clients")
def create(body: NewClient, x_lazusai_key: str | None = Header(default=None)):
    _auth(x_lazusai_key)
    cid = clients.unique_client_id(body.business_name)
    cfg = {
        "client_id": cid,
        "business_name": body.business_name,
        "industry": body.industry,
        "apple_id_number": body.apple_id_number,
        "bluebubbles_chat_guid": body.bluebubbles_chat_guid
        or f"iMessage;-;{body.apple_id_number}",
        "owner_telegram": body.owner_telegram,
        "hours": body.hours,
        "services": body.services,
        "pricing": body.pricing,
        "faqs": body.faqs,
        "escalation_keywords": body.escalation_keywords
        or ["complaint", "refund", "manager", "emergency", "lawyer"],
        "ai_personality": body.ai_personality
        or f"Friendly, concise front-desk assistant for {body.business_name}.",
        "booking_enabled": body.booking_enabled,
        "slot_minutes": body.slot_minutes or 30,
        "services_matrix": body.services_matrix,
        "staff": body.staff,
        "integrations": body.integrations,
        "dashboard_user": cid,
        "active": True,
        "created_at": clients._now(),
    }
    clients.save_client(cfg)
    chroma_store.get_collection(cid)  # create the tenant collection
    _reindex(cid, cfg)
    # Best-effort: register routing/config into the Worker KV so the new client
    # starts receiving messages immediately. Falls back to sync script if CF
    # creds aren't configured.
    kv_synced = cf_kv.push_client(cfg)
    return {"client_id": cid, "config": cfg, "kv_synced": kv_synced}


@app.get("/clients/{client_id}/config")
def get_config(client_id: str, x_lazusai_key: str | None = Header(default=None)):
    _auth(x_lazusai_key)
    return clients.load_client(client_id)


@app.post("/clients/{client_id}/config")
def update_config(client_id: str, body: ConfigUpdate, x_lazusai_key: str | None = Header(default=None)):
    _auth(x_lazusai_key)
    cfg = clients.load_client(client_id)
    for field, value in body.model_dump(exclude_none=True).items():
        cfg[field] = value
    clients.save_client(cfg)
    _reindex(client_id, cfg)
    return {"ok": True, "config": cfg}


@app.get("/clients/{client_id}/feed")
def feed(client_id: str, limit: int = 50, x_lazusai_key: str | None = Header(default=None)):
    _auth(x_lazusai_key)
    return {"turns": chroma_store.recent_turns(client_id, limit=limit)}


@app.get("/clients/{client_id}/leads")
def get_leads(client_id: str, today: bool = False, x_lazusai_key: str | None = Header(default=None)):
    _auth(x_lazusai_key)
    if today:
        return {"leads": clients.todays_leads(client_id)}
    path = clients.LEADS_DIR / client_id / "leads.json"
    import json
    leads = json.loads(path.read_text()) if path.exists() else []
    return {"leads": leads}


@app.post("/clients/{client_id}/leads")
def add_lead(client_id: str, body: Lead, x_lazusai_key: str | None = Header(default=None)):
    _auth(x_lazusai_key)
    lead = body.model_dump()
    clients.append_lead(client_id, lead)
    chroma_store.add_lead(client_id, lead)
    return {"ok": True}


@app.post("/clients/{client_id}/turns")
def add_turn(client_id: str, body: Turn, x_lazusai_key: str | None = Header(default=None)):
    _auth(x_lazusai_key)
    tid = chroma_store.log_turn(client_id, body.role, body.text, body.sender)
    return {"ok": True, "turn_id": tid}


@app.post("/clients/{client_id}/reindex")
def reindex(client_id: str, x_lazusai_key: str | None = Header(default=None)):
    _auth(x_lazusai_key)
    cfg = clients.load_client(client_id)
    n = _reindex(client_id, cfg)
    return {"ok": True, "indexed": n}


@app.post("/clients/{client_id}/toggle")
def toggle(client_id: str, active: bool | None = None, x_lazusai_key: str | None = Header(default=None)):
    """Flip active state, or set it explicitly with ?active=true|false
    (Hermes `pause` passes active=false)."""
    _auth(x_lazusai_key)
    cfg = clients.load_client(client_id)
    cfg["active"] = (not cfg.get("active", True)) if active is None else active
    clients.save_client(cfg)
    cf_kv.push_client(cfg)  # keep KV copy in sync so the Worker honors it
    return {"ok": True, "active": cfg["active"]}


@app.post("/clients/{client_id}/summary")
def summary(client_id: str, x_lazusai_key: str | None = Header(default=None)):
    """Summarize the last 24h of conversation for the morning digest."""
    _auth(x_lazusai_key)
    cfg = clients.load_client(client_id)
    since = time.time() - 24 * 3600
    turns = chroma_store.turns_since(client_id, since)
    leads = clients.todays_leads(client_id)
    transcript = "\n".join(f"[{t.get('role')}] {t.get('text')}" for t in turns)
    if not transcript:
        return {"summary": f"No conversations yesterday for {cfg['business_name']}.",
                "total_conversations": 0, "leads": len(leads)}
    messages = [
        {"role": "system", "content": (
            "You write a concise daily business summary for a local service "
            "business owner. Output: total conversations, leads captured, the "
            "most common questions, and anything unresolved that needs the "
            "owner's attention. Keep it under 150 words, plain text.")},
        {"role": "user", "content": (
            f"Business: {cfg['business_name']}\nLeads captured: {len(leads)}\n"
            f"Conversation log (last 24h):\n{transcript}")},
    ]
    result = nim_client.chat(messages, temperature=0.3, max_tokens=350)
    return {
        "summary": result.text,
        "model": result.model,
        "total_conversations": _count_conversations(turns),
        "leads": len(leads),
    }


# -------------------------------------------------------------------- bookings
@app.get("/clients/{client_id}/availability")
def get_availability(
    client_id: str,
    date: str,
    service: str = "",
    staff: str = "",
    x_lazusai_key: str | None = Header(default=None),
):
    """Open appointment slots for a date. n8n calls this to offer times.
    ?service= resolves duration from services_matrix; ?staff= narrows to one
    team member."""
    _auth(x_lazusai_key)
    cfg = clients.load_client(client_id)
    svc = _service_from_matrix(cfg, service) if service else None
    duration = int((svc or {}).get("duration_min") or cfg.get("slot_minutes") or 30)
    slots = bookings.availability(
        client_id, date,
        duration_min=duration,
        business_hours=cfg.get("hours") or {},
        staff_list=cfg.get("staff") or [],
        staff=staff or None,
        slot_minutes=int(cfg.get("slot_minutes") or 30),
        service_name=service or None,
    )
    return {"date": date, "service": service, "duration_min": duration, "slots": slots}


@app.get("/clients/{client_id}/bookings")
def get_bookings(
    client_id: str,
    date: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    staff: str | None = None,
    status: str | None = None,
    x_lazusai_key: str | None = Header(default=None),
):
    _auth(x_lazusai_key)
    return {
        "bookings": bookings.list_bookings(
            client_id, date=date, date_from=date_from, date_to=date_to,
            staff=staff, status=status,
        ),
        "stats": bookings.stats(client_id),
    }


@app.post("/clients/{client_id}/bookings")
def create_booking(client_id: str, body: NewBooking, x_lazusai_key: str | None = Header(default=None)):
    """Create a booking. Resolves price/duration from services_matrix, mints a
    payment link when the client requires a deposit/full payment, persists the
    booking, and alerts the assigned staff + owner. This is the single call n8n
    makes once the customer has confirmed service + time."""
    _auth(x_lazusai_key)
    cfg = clients.load_client(client_id)
    if not cfg.get("booking_enabled"):
        raise HTTPException(status_code=400, detail="booking_disabled")

    svc = _service_from_matrix(cfg, body.service)
    duration = body.service_duration_min or int((svc or {}).get("duration_min") or cfg.get("slot_minutes") or 30)
    price = body.service_price if body.service_price is not None else float((svc or {}).get("price") or 0)

    # Resolve payment up-front amount from the client's model (+ per-service deposit override).
    pay_cfg = payments.payment_config(cfg)
    if svc and svc.get("deposit") is not None and (pay_cfg.get("model") == "deposit"):
        upfront = float(svc["deposit"])
    else:
        upfront = payments.amount_for(pay_cfg, price)

    data = body.model_dump()
    data.update({"service_duration_min": duration, "service_price": price,
                 "amount_due": price, "deposit_amount": upfront if pay_cfg.get("model") == "deposit" else 0})

    try:
        booking = bookings.create(client_id, data)
    except ValueError as e:
        if str(e) == "slot_taken":
            raise HTTPException(status_code=409, detail="slot_taken")
        raise HTTPException(status_code=422, detail=str(e))

    # Payment link (best-effort; booking is already saved).
    payment = {"required": False}
    if payments.is_enabled(cfg) and upfront > 0:
        label = ("Deposit" if pay_cfg.get("model") == "deposit" else "Payment") + f" — {body.service}"
        link, err = payments.create_payment_link(
            cfg, amount=upfront, description=label,
            reference_id=booking["id"], buyer_phone=body.customer_phone,
        )
        payment = {"required": True, "amount": upfront, "model": pay_cfg.get("model"),
                   "url": link, "error": err}
        if link:
            bookings.update(client_id, booking["id"],
                            {"payment_link": link, "payment_status": "deposit_pending"
                             if pay_cfg.get("model") == "deposit" else "unpaid"})
            booking = bookings.get_booking(client_id, booking["id"])

    # Staff + owner alerts.
    alerts = {"staff": [], "owner": False}
    if body.notify:
        alerts = notify.notify_staff_of_booking(cfg, booking)

    return {"ok": True, "booking": booking, "payment": payment, "alerts": alerts}


@app.patch("/clients/{client_id}/bookings/{booking_id}")
def patch_booking(client_id: str, booking_id: str, body: BookingPatch, x_lazusai_key: str | None = Header(default=None)):
    _auth(x_lazusai_key)
    updated = bookings.update(client_id, booking_id, body.model_dump(exclude_none=True))
    if not updated:
        raise HTTPException(status_code=404, detail="not_found")
    return {"ok": True, "booking": updated}


@app.post("/clients/{client_id}/bookings/{booking_id}/cancel")
def cancel_booking(client_id: str, booking_id: str, x_lazusai_key: str | None = Header(default=None)):
    _auth(x_lazusai_key)
    updated = bookings.cancel(client_id, booking_id)
    if not updated:
        raise HTTPException(status_code=404, detail="not_found")
    return {"ok": True, "booking": updated}


@app.get("/clients/{client_id}/identify")
def identify_sender(client_id: str, phone: str, x_lazusai_key: str | None = Header(default=None)):
    """Tell n8n whether an inbound number is staff or a customer, so the bot can
    switch persona (staff get schedule/booking-management, customers get sales)."""
    _auth(x_lazusai_key)
    cfg = clients.load_client(client_id)
    norm = _norm_phone(phone)
    for s in cfg.get("staff") or []:
        if _norm_phone(s.get("phone", "")) == norm and norm:
            return {"role": "staff", "name": s.get("name"), "staff_role": s.get("role", "")}
    return {"role": "customer"}


def _norm_phone(s: str) -> str:
    return "".join(ch for ch in str(s or "") if ch.isdigit())[-10:]


# --------------------------------------------------------------------- helpers
def _reindex(client_id: str, cfg: dict) -> int:
    """Turn structured config (services, pricing, FAQs, hours) into context
    documents and rebuild the tenant's ChromaDB context."""
    docs = []
    if cfg.get("services"):
        docs.append({"id": "services", "text": "Services offered: " + ", ".join(cfg["services"])})
    if cfg.get("pricing"):
        pricing = "; ".join(f"{k}: {v}" for k, v in cfg["pricing"].items())
        docs.append({"id": "pricing", "text": "Pricing: " + pricing})
    if cfg.get("hours"):
        hours = "; ".join(f"{k}: {v}" for k, v in cfg["hours"].items())
        docs.append({"id": "hours", "text": "Hours of operation: " + hours})
    for i, faq in enumerate(cfg.get("faqs", [])):
        docs.append({"id": f"faq-{i}", "text": f"Q: {faq.get('q')}\nA: {faq.get('a')}"})
    # Booking-aware context so the bot knows what it can schedule and with whom.
    if cfg.get("booking_enabled"):
        matrix = cfg.get("services_matrix") or []
        if matrix:
            lines = []
            for s in matrix:
                bits = [s.get("name", "")]
                if s.get("price") is not None:
                    bits.append(f"${s['price']}")
                if s.get("duration_min"):
                    bits.append(f"{s['duration_min']} min")
                if s.get("staff"):
                    bits.append("with " + "/".join(s["staff"]))
                lines.append(" — ".join(b for b in bits if b))
            docs.append({"id": "bookable-services",
                         "text": "Bookable services (name — price — duration — staff):\n" + "\n".join(lines)})
        staff = cfg.get("staff") or []
        if staff:
            roster = "; ".join(
                f"{s.get('name')}" + (f" ({s.get('role')})" if s.get('role') else "")
                for s in staff
            )
            docs.append({"id": "team-roster", "text": "Team members who take appointments: " + roster})
        pay = payments.payment_config(cfg)
        if (pay.get("model") or "none") != "none":
            if pay.get("model") == "deposit":
                docs.append({"id": "booking-payment",
                             "text": f"A deposit of ${pay.get('deposit_amount', 0)} is required to hold a booking; the balance is paid at the appointment."})
            elif pay.get("model") == "full":
                docs.append({"id": "booking-payment",
                             "text": "Bookings are paid in full at the time of booking via a secure payment link."})
    return chroma_store.reindex_context(client_id, docs)


def _service_from_matrix(cfg: dict, name: str) -> dict | None:
    """Case-insensitive lookup of a bookable service by name."""
    want = (name or "").strip().lower()
    for s in cfg.get("services_matrix") or []:
        if s.get("name", "").strip().lower() == want:
            return s
    return None


def _staff_names(cfg: dict) -> set[str]:
    return {s.get("name") for s in (cfg.get("staff") or []) if s.get("name")}


def _messages_today(client_id: str) -> int:
    today_start = time.mktime(time.strptime(time.strftime("%Y-%m-%d"), "%Y-%m-%d"))
    return len(chroma_store.turns_since(client_id, today_start))


def _count_conversations(turns: list[dict]) -> int:
    senders = {t.get("sender") for t in turns if t.get("sender")}
    return len(senders) or (1 if turns else 0)
