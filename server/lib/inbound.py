"""Inbound iMessage pipeline helpers.

Python port of the routing/parsing done by the Cloudflare worker
(src/worker.js) and the AI pipeline from the n8n inbound workflow
(n8n/workflow-1-inbound-handler.json), so the Core API can process a
BlueBubbles webhook end-to-end by itself: parse -> route -> transcribe ->
prompt -> LLM (with booking tool-loop) -> reply -> log -> lead detection.

The functions here are pure/IO-light so they can be unit-tested; the
orchestration lives in core_api/app.py's /webhook endpoint.

Environment (set in /etc/lazusai/core.env):
  WHISPER_URL           e.g. http://127.0.0.1:8002 (blank = skip voice notes)
  BLUEBUBBLES_URL       BlueBubbles server base URL (blank = replies logged only)
  BLUEBUBBLES_PASSWORD  BlueBubbles server password
  TZ_OFFSET_MIN         business-local offset from UTC in minutes (for booking dates)
"""
from __future__ import annotations

import os
import re
from datetime import datetime, timedelta, timezone

import httpx

AUDIO_EXTENSIONS = (".caf", ".m4a", ".mp3", ".wav", ".amr", ".aac", ".ogg", ".opus")
AUDIO_MIME_PREFIX = "audio/"
REQUEST_TIMEOUT = 30.0

FALLBACK_REPLY = "Thanks for your message! Someone from our team will get back to you shortly."


def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default)


def today_iso() -> str:
    """Business-local date, shifted by TZ_OFFSET_MIN from UTC."""
    tz_min = 0
    try:
        tz_min = int(_env("TZ_OFFSET_MIN", "0") or 0)
    except ValueError:
        pass
    now = datetime.now(timezone.utc) + timedelta(minutes=tz_min)
    return now.date().isoformat()


# --------------------------------------------------------------------- parse

def normalize_phone(s: str) -> str:
    digits = re.sub(r"[^\d+]", "", s or "")
    if digits and not digits.startswith("+") and len(digits) == 10:
        digits = "+1" + digits
    return digits


def parse_webhook(event: dict) -> dict:
    """Normalize a BlueBubbles webhook into routable fields.

    Returns {"ignored": <reason>} for events we should skip, else a dict with
    sender / chat_guid / text / guid / attachments / voice_note.
    """
    if not isinstance(event, dict):
        return {"ignored": "invalid_payload"}

    etype = event.get("type") or event.get("event") or ""
    if etype and not re.search(r"new[-_]?message", etype, re.I):
        return {"ignored": etype}

    message = event.get("data") or event.get("message") or event
    if not isinstance(message, dict):
        return {"ignored": "invalid_payload"}

    if message.get("isFromMe") is True or message.get("is_from_me") is True:
        return {"ignored": "from_me"}

    handle = message.get("handle") or message.get("from") or {}
    if not isinstance(handle, dict):
        handle = {}
    sender = (
        handle.get("address")
        or handle.get("phone")
        or message.get("address")
        or message.get("sender")
        or ""
    )

    chat_guid = ""
    chats = message.get("chats") or message.get("chat") or []
    if isinstance(chats, list) and chats:
        chat_guid = chats[0].get("guid") or chats[0].get("chatGuid") or ""
    elif isinstance(chats, dict):
        chat_guid = chats.get("guid") or ""
    chat_guid = chat_guid or message.get("chatGuid") or message.get("chat_guid") or ""

    attachments = []
    voice_note = False
    for att in message.get("attachments") or []:
        name = att.get("transferName") or att.get("transfer_name") or att.get("name") or ""
        mime = att.get("mimeType") or att.get("mime_type") or att.get("uti") or ""
        guid = att.get("guid") or att.get("attachmentGuid") or ""
        is_audio = name.lower().endswith(AUDIO_EXTENSIONS) or (
            isinstance(mime, str) and mime.startswith(AUDIO_MIME_PREFIX)
        )
        if is_audio:
            voice_note = True
        attachments.append({
            "name": name,
            "mime": mime,
            "is_audio": is_audio,
            "url": f"/api/v1/attachment/{guid}/download" if guid else att.get("url", ""),
        })

    if not sender and not chat_guid:
        return {"ignored": "no_sender"}

    return {
        "sender": sender,
        "chat_guid": chat_guid,
        "text": message.get("text") or message.get("body") or "",
        "guid": message.get("guid") or message.get("messageGuid") or "",
        "attachments": attachments,
        "voice_note": voice_note,
    }


def route_client(parsed: dict, routes: dict) -> str | None:
    """Map an inbound message to a client_id via the routing index.

    Multi-tenant isolation: if no identifier matches, return None — never guess.
    """
    for ident in (
        parsed.get("chat_guid"),
        parsed.get("sender"),
        normalize_phone(parsed.get("sender", "")),
    ):
        if ident and ident in routes:
            return routes[ident]
    return None


# --------------------------------------------------------------- transcription

def transcribe_voice_note(parsed: dict) -> str:
    """Send the first audio attachment to the Whisper service. Returns the
    transcript, or "" if transcription is unavailable/failed."""
    whisper = _env("WHISPER_URL").rstrip("/")
    if not whisper:
        return ""
    audio = next((a for a in parsed.get("attachments", []) if a.get("is_audio")), None)
    if not audio:
        return ""
    url = audio.get("url", "")
    bb = _env("BLUEBUBBLES_URL").rstrip("/")
    if url.startswith("/") and bb:
        url = bb + url
    try:
        resp = httpx.post(
            f"{whisper}/transcribe",
            json={"url": url, "filename": audio.get("name") or "audio.m4a"},
            timeout=120.0,
        )
        resp.raise_for_status()
        return (resp.json() or {}).get("text", "") or ""
    except Exception:  # noqa: BLE001 — a failed transcription must not kill the reply
        return ""


# -------------------------------------------------------------------- prompts

def build_context_block(cfg: dict) -> str:
    lines = []
    if cfg.get("services"):
        lines.append("Services: " + ", ".join(cfg["services"]))
    if cfg.get("pricing"):
        lines.append("Pricing: " + "; ".join(f"{k}: {v}" for k, v in cfg["pricing"].items()))
    if cfg.get("hours"):
        lines.append("Hours: " + "; ".join(f"{k} {v}" for k, v in cfg["hours"].items()))
    for f in cfg.get("faqs") or []:
        lines.append(f"FAQ — {f.get('q', '')} {f.get('a', '')}")
    return "\n".join(lines)


def build_booking_block(cfg: dict, sender: str, today: str) -> str:
    if not cfg.get("booking_enabled"):
        return ""
    matrix_lines = []
    for s in cfg.get("services_matrix") or []:
        bits = [s.get("name", "")]
        if s.get("price") is not None:
            bits.append(f"${s['price']}")
        if s.get("duration_min"):
            bits.append(f"{s['duration_min']}min")
        if s.get("staff"):
            bits.append("by " + "/".join(s["staff"]))
        matrix_lines.append("• " + " — ".join(bits))
    matrix = "\n".join(matrix_lines)
    team = ", ".join(
        s.get("name", "") + (f" ({s['role']})" if s.get("role") else "")
        for s in cfg.get("staff") or []
    )
    pay = ((cfg.get("integrations") or {}).get("payment")) or {}
    pay_note = ""
    if pay.get("model") == "deposit":
        pay_note = (f"A deposit of ${pay.get('deposit_amount', 0)} is required; "
                    "a payment link will be sent automatically after booking.")
    elif pay.get("model") == "full":
        pay_note = "Full payment is collected via a link sent automatically after booking."

    parts = [
        f"\nBOOKING IS ENABLED. Today is {today}.",
        f"Bookable services:\n{matrix}" if matrix else "",
        f"Team: {team}" if team else "",
        pay_note,
        "\nTo help a customer book, gather: service, preferred day (resolve to a "
        "YYYY-MM-DD date), optional staff, their name, and phone.",
        "When you need to see open times, end your reply with EXACTLY one line:",
        '[[AVAIL service="<name>" date="<YYYY-MM-DD>" staff="<name or empty>"]]',
        "When the customer has confirmed a specific service, date, time, and given "
        "their name, end your reply with EXACTLY one line:",
        f'[[BOOK service="<name>" staff="<name or empty>" date="<YYYY-MM-DD>" '
        f'start="<HH:MM 24h>" name="<customer name>" phone="{sender}" notes="<optional>"]]',
        "Never invent open times — only offer times returned by an AVAIL result. "
        "Do not show the [[...]] line's raw text as the whole message; put your "
        "normal reply first, then the directive on its own final line.",
    ]
    return "\n".join(p for p in parts if p)


def build_customer_prompt(cfg: dict, sender: str, today: str) -> str:
    return (
        f"You are the iMessage assistant for {cfg.get('business_name', '')} "
        f"({cfg.get('industry', '')}).\n"
        f"Personality & rules: {cfg.get('ai_personality', '')}\n\n"
        f"Business context:\n{build_context_block(cfg)}\n"
        f"{build_booking_block(cfg, sender, today)}\n\n"
        "Answer as the business. Be concise (SMS-length). Write plain "
        "conversational text only — no markdown, no asterisks, no bullet "
        "points; this is a text message. If you cannot help, offer to have "
        "the owner follow up and capture the customer's name and contact "
        "details."
    )


def build_staff_prompt(cfg: dict, who: dict, todays_bookings: list[dict],
                       sender: str, today: str) -> str:
    sched = "\n".join(
        f"{b.get('start')} {b.get('service')} — "
        f"{b.get('customer_name') or b.get('customer_phone') or ''} ({b.get('status')})"
        for b in todays_bookings
    ) or "nothing booked yet"
    role = f" ({who['staff_role']})" if who.get("staff_role") else ""
    return (
        f"You are the LazusAI staff assistant for {cfg.get('business_name', '')}. "
        f"You are talking to {who.get('name') or 'a team member'}{role}, a STAFF "
        "member — not a customer. Be brief and helpful.\n"
        f"Today's schedule for them ({today}):\n{sched}\n\n"
        "You can answer questions about their day and the business. If they ask "
        "to change a booking, tell them to use the dashboard for now."
        f"{build_booking_block(cfg, sender, today)}"
    )


# ------------------------------------------------------------------ directives

_DIRECTIVE_RE = re.compile(r"\[\[(AVAIL|BOOK)\b([^\]]*)\]\]", re.I)
_ARGS_RE = re.compile(r'(\w+)\s*=\s*"([^"]*)"')


def parse_directive(text: str) -> dict | None:
    m = _DIRECTIVE_RE.search(text or "")
    if not m:
        return None
    return {
        "verb": m.group(1).upper(),
        "args": dict(_ARGS_RE.findall(m.group(2))),
        "raw": m.group(0),
    }


def strip_directive(text: str) -> str:
    return _DIRECTIVE_RE.sub("", text or "").strip()


_MD_EMPHASIS_RE = re.compile(r"(\*\*|__|(?<!\w)\*(?!\s)|(?<!\w)_(?!\s))")
_MD_HEADING_RE = re.compile(r"^#{1,6}\s+", re.M)


def clean_sms(text: str) -> str:
    """Strip markdown artifacts — iMessage renders them as literal symbols."""
    out = _MD_HEADING_RE.sub("", text or "")
    out = out.replace("**", "").replace("__", "").replace("`", "")
    out = re.sub(r"^\s*[-•]\s+", "", out, flags=re.M)
    return re.sub(r"\n{3,}", "\n\n", out).strip()


# ------------------------------------------------------------------ lead check

_EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")
_PHONE_RE = re.compile(r"(\+?\d[\d\s().-]{7,}\d)")
_NAME_RE = re.compile(r"(?:my name is|i am|i'm|this is)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)", re.I)


def detect_lead(text: str, sender: str) -> dict | None:
    """WF2's lead heuristics: name / phone / email mentioned in the message."""
    email = _EMAIL_RE.search(text or "")
    phone = _PHONE_RE.search(text or "")
    name = _NAME_RE.search(text or "")
    if not (email or phone or name):
        return None
    return {
        "sender": sender,
        "name": name.group(1) if name else "",
        "phone": phone.group(1).strip() if phone else sender,
        "email": email.group(0) if email else "",
        "message": text,
    }


def escalated(cfg: dict, text: str) -> bool:
    hay = (text or "").lower()
    return any(k.lower() in hay for k in cfg.get("escalation_keywords") or [])


# ---------------------------------------------------------------- BlueBubbles

def send_imessage_reply(chat_guid: str, message: str) -> bool:
    """Deliver the reply over iMessage via the BlueBubbles REST API.

    Returns False (without raising) when BlueBubbles isn't configured yet, so
    the pipeline still logs turns during pre-launch testing.
    """
    bb = _env("BLUEBUBBLES_URL").rstrip("/")
    pw = _env("BLUEBUBBLES_PASSWORD")
    if not bb or not chat_guid:
        return False
    try:
        resp = httpx.post(
            f"{bb}/api/v1/message/text",
            params={"password": pw},
            json={
                "chatGuid": chat_guid,
                "tempGuid": f"lazusai-{int(datetime.now().timestamp() * 1000)}",
                "message": message,
                "method": "private-api",
            },
            timeout=REQUEST_TIMEOUT,
        )
        return resp.status_code < 300
    except Exception:  # noqa: BLE001
        return False
