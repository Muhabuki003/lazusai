"""Booking store + availability engine. File-backed source of truth on the VPS,
mirroring the leads store (lib/clients.append_lead).

Layout (relative to LAZUSAI_DATA_DIR, default ./data):
  bookings/<client_id>/bookings.json    append-only, mutable booking log

A booking record:
  {
    "id":              "bk_<ts>_<rand>",
    "client_id":       "acme-barbers",
    "service":         "Haircut",
    "service_price":   35,             # dollars (number)
    "service_duration_min": 30,
    "staff":           "Josh",          # "" / "any" if no preference
    "date":            "2026-07-02",    # local ISO date (YYYY-MM-DD)
    "start":           "14:00",         # 24h HH:MM (business-local)
    "end":             "14:30",
    "customer_name":   "Jacob",
    "customer_phone":  "+15551234567",
    "customer_email":  "",
    "address":         "",              # for mobile/home-service jobs
    "notes":           "",
    "status":          "confirmed",     # pending|confirmed|completed|cancelled|no_show
    "payment_status":  "none",          # none|deposit_pending|deposit_paid|paid|refunded
    "amount_due":      35,
    "deposit_amount":  0,
    "payment_link":    "",
    "source":          "bot",           # bot|dashboard|web
    "created_at":      "...Z",
    "updated_at":      "...Z"
  }

Staff and the bookable services matrix live in the client config (see
lib/clients + the Core API). This module only owns booking records and the
availability math derived from staff schedules + service durations.
"""
from __future__ import annotations

import json
import os
import re
import time
import uuid
from pathlib import Path

DATA_DIR = Path(os.environ.get("LAZUSAI_DATA_DIR", "data"))
BOOKINGS_DIR = DATA_DIR / "bookings"

# Slots we consider "blocking" when computing availability.
ACTIVE_STATUSES = {"pending", "confirmed", "completed"}
# Default booking grid granularity (minutes) when a client sets none.
DEFAULT_SLOT_MINUTES = 30

_DOW = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


# ------------------------------------------------------------------ time utils
def to_minutes(hhmm: str) -> int:
    """'14:30' -> 870. Accepts H:MM or HH:MM."""
    h, m = str(hhmm).strip().split(":")
    return int(h) * 60 + int(m)


def to_hhmm(minutes: int) -> str:
    """870 -> '14:30'."""
    minutes %= 24 * 60
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def weekday_key(date_iso: str) -> str:
    """'2026-07-02' -> 'thu' (matches the hours dict keys)."""
    y, m, d = (int(x) for x in date_iso.split("-"))
    # 0 = Monday from time.struct_time via calendar-independent formula.
    wd = time.strptime(f"{y:04d}-{m:02d}-{d:02d}", "%Y-%m-%d").tm_wday
    return _DOW[wd]


def parse_window(spec: str) -> tuple[int, int] | None:
    """'08:00-17:00' -> (480, 1020). 'closed'/'' -> None."""
    if not spec or str(spec).strip().lower() in ("closed", "off", "none"):
        return None
    m = re.match(r"\s*(\d{1,2}:\d{2})\s*-\s*(\d{1,2}:\d{2})\s*$", str(spec))
    if not m:
        return None
    return to_minutes(m.group(1)), to_minutes(m.group(2))


def overlaps(a_start: int, a_end: int, b_start: int, b_end: int) -> bool:
    return a_start < b_end and b_start < a_end


# ------------------------------------------------------------------ file store
def _dir(client_id: str) -> Path:
    return BOOKINGS_DIR / client_id


def _path(client_id: str) -> Path:
    return _dir(client_id) / "bookings.json"


def _read(client_id: str) -> list[dict]:
    path = _path(client_id)
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError:
        return []


def _write(client_id: str, rows: list[dict]) -> None:
    _dir(client_id).mkdir(parents=True, exist_ok=True)
    _path(client_id).write_text(json.dumps(rows, indent=2))


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _new_id() -> str:
    return f"bk_{int(time.time()*1000)}_{uuid.uuid4().hex[:6]}"


# --------------------------------------------------------------------- queries
def list_bookings(
    client_id: str,
    *,
    date: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    staff: str | None = None,
    status: str | None = None,
) -> list[dict]:
    """Return bookings, optionally filtered, sorted by (date, start)."""
    rows = _read(client_id)
    if date:
        rows = [r for r in rows if r.get("date") == date]
    if date_from:
        rows = [r for r in rows if r.get("date", "") >= date_from]
    if date_to:
        rows = [r for r in rows if r.get("date", "") <= date_to]
    if staff:
        rows = [r for r in rows if r.get("staff") == staff]
    if status:
        rows = [r for r in rows if r.get("status") == status]
    rows.sort(key=lambda r: (r.get("date", ""), r.get("start", "")))
    return rows


def get_booking(client_id: str, booking_id: str) -> dict | None:
    for r in _read(client_id):
        if r.get("id") == booking_id:
            return r
    return None


def todays_bookings(client_id: str) -> list[dict]:
    return list_bookings(client_id, date=time.strftime("%Y-%m-%d"))


# ---------------------------------------------------------------- availability
def _staff_window(staff_cfg: dict, business_hours: dict, day: str) -> tuple[int, int] | None:
    """Working window (minutes) for a staff member on a weekday, falling back
    to business hours when the staff member has no explicit schedule."""
    hours = staff_cfg.get("hours") or {}
    spec = hours.get(day)
    if spec is None:
        spec = (business_hours or {}).get(day)
    return parse_window(spec)


def staff_for_service(staff_list: list[dict], service_name: str) -> list[dict]:
    """Staff who can perform a service. A staff member with an empty `services`
    list is treated as able to do everything."""
    out = []
    for s in staff_list or []:
        svcs = s.get("services") or []
        if not svcs or service_name in svcs:
            out.append(s)
    return out


def availability(
    client_id: str,
    date: str,
    *,
    duration_min: int,
    business_hours: dict,
    staff_list: list[dict] | None = None,
    staff: str | None = None,
    slot_minutes: int = DEFAULT_SLOT_MINUTES,
    service_name: str | None = None,
    now_ts: float | None = None,
) -> list[dict]:
    """Compute open slots for `date`.

    Returns a list of {"start", "end", "staff"} dicts. When there is a staff
    roster, each open slot is attributed to a specific available staff member;
    with no roster the business is treated as a single resource.

    Past slots (relative to now_ts, if the date is today) are excluded.
    """
    staff_list = staff_list or []
    existing = [r for r in list_bookings(client_id, date=date)
                if r.get("status") in ACTIVE_STATUSES]

    # Candidate resources: matching staff, or a single anonymous resource.
    if staff_list:
        candidates = staff_for_service(staff_list, service_name) if service_name else list(staff_list)
        if staff and staff != "any":
            candidates = [s for s in candidates if s.get("name") == staff]
    else:
        candidates = [{"name": "", "hours": {}}]

    day = weekday_key(date)
    step = max(5, int(slot_minutes))
    dur = max(5, int(duration_min))

    # Cutoff for past slots when date == today.
    now_ts = time.time() if now_ts is None else now_ts
    is_today = date == time.strftime("%Y-%m-%d", time.localtime(now_ts))
    now_min = (time.localtime(now_ts).tm_hour * 60 + time.localtime(now_ts).tm_min) if is_today else -1

    slots: list[dict] = []
    seen: set[tuple[str, str]] = set()  # (start, staff) dedupe
    for cand in candidates:
        window = _staff_window(cand, business_hours, day)
        if not window:
            continue
        open_min, close_min = window
        booked = [(to_minutes(r["start"]), to_minutes(r["end"]))
                  for r in existing
                  if (not cand.get("name") or r.get("staff") in ("", "any", cand["name"]))
                  and r.get("start") and r.get("end")]
        t = open_min
        while t + dur <= close_min:
            if t >= now_min and not any(overlaps(t, t + dur, bs, be) for bs, be in booked):
                key = (to_hhmm(t), cand.get("name", ""))
                if key not in seen:
                    seen.add(key)
                    slots.append({"start": to_hhmm(t), "end": to_hhmm(t + dur),
                                  "staff": cand.get("name", "")})
            t += step

    slots.sort(key=lambda s: (s["start"], s["staff"]))
    return slots


def is_slot_open(
    client_id: str,
    date: str,
    start: str,
    duration_min: int,
    *,
    staff: str | None = None,
) -> bool:
    """True if [start, start+duration) on `date` is free for `staff`
    (or for anyone if staff is unset). Used to guard create()."""
    s = to_minutes(start)
    e = s + max(5, int(duration_min))
    for r in list_bookings(client_id, date=date):
        if r.get("status") not in ACTIVE_STATUSES:
            continue
        if staff and staff != "any" and r.get("staff") not in ("", "any", staff):
            continue
        if r.get("start") and r.get("end") and overlaps(s, e, to_minutes(r["start"]), to_minutes(r["end"])):
            return False
    return True


# ----------------------------------------------------------------- mutations
def create(client_id: str, data: dict, *, enforce_availability: bool = True) -> dict:
    """Create a booking. Computes `end` from start+duration when absent.
    Raises ValueError('slot_taken') if the slot conflicts and enforcement is on.
    """
    duration = int(data.get("service_duration_min") or 30)
    start = data.get("start")
    if not data.get("date") or not start:
        raise ValueError("date and start are required")

    if not data.get("end"):
        data["end"] = to_hhmm(to_minutes(start) + duration)

    if enforce_availability and not is_slot_open(
        client_id, data["date"], start, duration, staff=data.get("staff")
    ):
        raise ValueError("slot_taken")

    now = _now()
    booking = {
        "id": _new_id(),
        "client_id": client_id,
        "service": data.get("service", ""),
        "service_price": data.get("service_price", 0),
        "service_duration_min": duration,
        "staff": data.get("staff", "") or "",
        "date": data["date"],
        "start": start,
        "end": data["end"],
        "customer_name": data.get("customer_name", ""),
        "customer_phone": data.get("customer_phone", ""),
        "customer_email": data.get("customer_email", ""),
        "address": data.get("address", ""),
        "notes": data.get("notes", ""),
        "status": data.get("status", "confirmed"),
        "payment_status": data.get("payment_status", "none"),
        "amount_due": data.get("amount_due", data.get("service_price", 0)),
        "deposit_amount": data.get("deposit_amount", 0),
        "payment_link": data.get("payment_link", ""),
        "source": data.get("source", "bot"),
        "created_at": now,
        "updated_at": now,
    }
    rows = _read(client_id)
    rows.append(booking)
    _write(client_id, rows)
    return booking


def update(client_id: str, booking_id: str, patch: dict) -> dict | None:
    """Patch mutable fields of a booking. Returns the updated record or None."""
    rows = _read(client_id)
    updated = None
    allowed = {
        "service", "service_price", "service_duration_min", "staff", "date",
        "start", "end", "customer_name", "customer_phone", "customer_email",
        "address", "notes", "status", "payment_status", "amount_due",
        "deposit_amount", "payment_link", "reminded_at",
    }
    for r in rows:
        if r.get("id") == booking_id:
            for k, v in patch.items():
                if k in allowed and v is not None:
                    r[k] = v
            # Recompute end if start/duration changed but end wasn't given.
            if ("start" in patch or "service_duration_min" in patch) and "end" not in patch:
                r["end"] = to_hhmm(to_minutes(r["start"]) + int(r.get("service_duration_min") or 30))
            r["updated_at"] = _now()
            updated = r
            break
    if updated:
        _write(client_id, rows)
    return updated


def set_status(client_id: str, booking_id: str, status: str) -> dict | None:
    return update(client_id, booking_id, {"status": status})


def cancel(client_id: str, booking_id: str) -> dict | None:
    return set_status(client_id, booking_id, "cancelled")


def stats(client_id: str) -> dict:
    """Counts used by the dashboard cards."""
    rows = _read(client_id)
    today = time.strftime("%Y-%m-%d")
    upcoming = [r for r in rows if r.get("date", "") >= today
                and r.get("status") in ("pending", "confirmed")]
    return {
        "total": len(rows),
        "today": len([r for r in rows if r.get("date") == today]),
        "upcoming": len(upcoming),
        "no_shows": len([r for r in rows if r.get("status") == "no_show"]),
    }
