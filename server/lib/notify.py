"""Outbound notifications: Telegram (owner) and iMessage via BlueBubbles (staff).

Used by the Core API to alert the team when the bot books an appointment, e.g.
"Josh — Jacob booked a Haircut Thu Jul 2 at 5:00 PM (deposit paid)."

Config comes from the environment (set in /etc/lazusai/core.env):
  TELEGRAM_BOT_TOKEN     shared bot used for owner alerts + daily digest
  BLUEBUBBLES_URL        base URL of the BlueBubbles server (via tunnel/LAN)
  BLUEBUBBLES_PASSWORD   BlueBubbles server password

Every send is best-effort and returns a bool; a booking is never blocked by a
failed notification.
"""
from __future__ import annotations

import os

import httpx

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
BLUEBUBBLES_URL = os.environ.get("BLUEBUBBLES_URL", "")
BLUEBUBBLES_PASSWORD = os.environ.get("BLUEBUBBLES_PASSWORD", "")
REQUEST_TIMEOUT = 15.0


def send_telegram(chat: str, text: str, *, http_client: httpx.Client | None = None) -> bool:
    """Send a Telegram message to an owner chat id or @handle."""
    if not TELEGRAM_BOT_TOKEN or not chat:
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": chat, "text": text, "disable_web_page_preview": True}
    try:
        resp = _post(url, json=payload, http_client=http_client)
        return resp.status_code < 300
    except Exception:  # noqa: BLE001
        return False


def send_imessage(phone: str, text: str, *, http_client: httpx.Client | None = None) -> bool:
    """Send an iMessage to a phone number via the BlueBubbles server."""
    if not BLUEBUBBLES_URL or not phone:
        return False
    chat_guid = phone if phone.startswith("iMessage;") else f"iMessage;-;{phone}"
    url = f"{BLUEBUBBLES_URL.rstrip('/')}/api/v1/message/text"
    params = {"password": BLUEBUBBLES_PASSWORD} if BLUEBUBBLES_PASSWORD else {}
    body = {"chatGuid": chat_guid, "message": text, "method": "apple-script"}
    try:
        resp = _post(url, params=params, json=body, http_client=http_client)
        return resp.status_code < 300
    except Exception:  # noqa: BLE001
        return False


def notify_staff_of_booking(client_cfg: dict, booking: dict,
                            *, http_client: httpx.Client | None = None) -> dict:
    """Alert the assigned staff member (or all opted-in staff) + the owner.

    Returns {"staff": [names notified], "owner": bool}.
    """
    text = format_booking_alert(booking)
    staff_list = client_cfg.get("staff") or []
    assigned = (booking.get("staff") or "").strip()

    targets = []
    for s in staff_list:
        if s.get("notify") is False or not s.get("phone"):
            continue
        # If a specific barber/tech is assigned, only ping them; otherwise ping all.
        if assigned and assigned != "any" and s.get("name") != assigned:
            continue
        targets.append(s)

    notified = []
    for s in targets:
        if send_imessage(s["phone"], _personalize(s["name"], text), http_client=http_client):
            notified.append(s["name"])

    owner = send_telegram(client_cfg.get("owner_telegram", ""), text, http_client=http_client)
    return {"staff": notified, "owner": owner}


def _personalize(name: str, text: str) -> str:
    first = (name or "").split(" ")[0]
    return f"{first} — {text}" if first else text


def format_booking_alert(b: dict) -> str:
    when = format_when(b.get("date", ""), b.get("start", ""))
    who = b.get("customer_name") or b.get("customer_phone") or "A customer"
    svc = b.get("service") or "an appointment"
    line = f"{who} booked a {svc} {when}"
    pay = b.get("payment_status")
    if pay == "deposit_paid":
        line += " (deposit paid)"
    elif pay == "paid":
        line += " (paid in full)"
    elif pay in ("deposit_pending",):
        line += " (deposit pending)"
    if b.get("address"):
        line += f"\n📍 {b['address']}"
    if b.get("customer_phone"):
        line += f"\n📞 {b['customer_phone']}"
    if b.get("notes"):
        line += f"\n📝 {b['notes']}"
    return line


def format_when(date_iso: str, start_hhmm: str) -> str:
    """'2026-07-02','17:00' -> 'Thu Jul 2 at 5:00 PM'. Degrades gracefully."""
    import time as _t
    try:
        st = _t.strptime(date_iso, "%Y-%m-%d")
        day = _t.strftime("%a %b ", st) + str(st.tm_mday)
    except Exception:  # noqa: BLE001
        day = date_iso
    try:
        h, m = (int(x) for x in start_hhmm.split(":"))
        ampm = "AM" if h < 12 else "PM"
        h12 = h % 12 or 12
        clock = f"{h12}:{m:02d} {ampm}"
    except Exception:  # noqa: BLE001
        clock = start_hhmm
    if day and clock:
        return f"{day} at {clock}"
    return (day + " " + clock).strip()


def _post(url, *, json=None, params=None, http_client=None):
    if http_client is not None:
        return http_client.post(url, json=json, params=params)
    with httpx.Client(timeout=REQUEST_TIMEOUT) as c:
        return c.post(url, json=json, params=params)
