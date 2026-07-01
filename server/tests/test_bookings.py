"""Tests for the booking store + availability engine (lib/bookings).

Run: cd server && python -m pytest tests/test_bookings.py -q
(or plain `python tests/test_bookings.py` for a dependency-free smoke run).
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

# Point the store at a temp dir BEFORE importing the module.
_TMP = tempfile.mkdtemp(prefix="lazusai-bk-")
os.environ["LAZUSAI_DATA_DIR"] = _TMP
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib import bookings  # noqa: E402

BUSINESS_HOURS = {
    "mon": "09:00-17:00", "tue": "09:00-17:00", "wed": "09:00-17:00",
    "thu": "09:00-17:00", "fri": "09:00-17:00", "sat": "10:00-14:00",
    "sun": "closed",
}
STAFF = [
    {"name": "Josh", "phone": "+1555000001", "services": ["Haircut", "Fade"]},
    {"name": "Jacob", "phone": "+1555000002", "services": ["Haircut"]},
]
# A weekday far in the future so "today" cutoffs never interfere.
FUTURE_MON = "2099-01-05"   # a Monday
FUTURE_SUN = "2099-01-04"   # a Sunday


def _reset(cid):
    p = bookings._path(cid)
    if p.exists():
        p.unlink()


def test_time_utils():
    assert bookings.to_minutes("14:30") == 870
    assert bookings.to_hhmm(870) == "14:30"
    assert bookings.parse_window("09:00-17:00") == (540, 1020)
    assert bookings.parse_window("closed") is None
    assert bookings.weekday_key(FUTURE_MON) == "mon"
    assert bookings.weekday_key(FUTURE_SUN) == "sun"
    assert bookings.overlaps(600, 660, 630, 690) is True
    assert bookings.overlaps(600, 660, 660, 720) is False


def test_availability_empty_day():
    cid = "t-empty"
    _reset(cid)
    slots = bookings.availability(
        cid, FUTURE_MON, duration_min=30, business_hours=BUSINESS_HOURS,
        slot_minutes=30,
    )
    # 09:00..16:30 last start, 30-min grid, single anonymous resource = 16 slots
    assert slots[0]["start"] == "09:00"
    assert slots[-1]["start"] == "16:30"
    assert len(slots) == 16


def test_availability_closed_day():
    cid = "t-closed"
    _reset(cid)
    slots = bookings.availability(
        cid, FUTURE_SUN, duration_min=30, business_hours=BUSINESS_HOURS,
    )
    assert slots == []


def test_booking_blocks_slot():
    cid = "t-block"
    _reset(cid)
    bookings.create(cid, {
        "service": "Haircut", "service_duration_min": 60,
        "date": FUTURE_MON, "start": "10:00", "customer_name": "A",
    })
    slots = bookings.availability(
        cid, FUTURE_MON, duration_min=30, business_hours=BUSINESS_HOURS,
        slot_minutes=30,
    )
    starts = {s["start"] for s in slots}
    # 10:00 and 10:30 are inside the 10:00-11:00 booking → blocked.
    assert "10:00" not in starts
    assert "10:30" not in starts
    assert "09:30" in starts  # 09:30-10:00 ends exactly at booking start → ok
    assert "11:00" in starts


def test_per_staff_availability_and_service_filter():
    cid = "t-staff"
    _reset(cid)
    # Josh booked 09:00-09:30; Jacob still free at 09:00.
    bookings.create(cid, {
        "service": "Haircut", "service_duration_min": 30, "staff": "Josh",
        "date": FUTURE_MON, "start": "09:00", "customer_name": "A",
    })
    slots = bookings.availability(
        cid, FUTURE_MON, duration_min=30, business_hours=BUSINESS_HOURS,
        staff_list=STAFF, service_name="Haircut", slot_minutes=30,
    )
    at_9 = [s for s in slots if s["start"] == "09:00"]
    assert {s["staff"] for s in at_9} == {"Jacob"}  # Josh blocked, Jacob open

    # Fade is only offered by Josh → Jacob must not appear.
    fade = bookings.availability(
        cid, FUTURE_MON, duration_min=30, business_hours=BUSINESS_HOURS,
        staff_list=STAFF, service_name="Fade", slot_minutes=30,
    )
    assert all(s["staff"] == "Josh" for s in fade)


def test_double_booking_rejected():
    cid = "t-double"
    _reset(cid)
    bookings.create(cid, {
        "service": "Haircut", "service_duration_min": 60, "staff": "Josh",
        "date": FUTURE_MON, "start": "10:00", "customer_name": "A",
    })
    try:
        bookings.create(cid, {
            "service": "Haircut", "service_duration_min": 30, "staff": "Josh",
            "date": FUTURE_MON, "start": "10:30", "customer_name": "B",
        })
        assert False, "expected slot_taken"
    except ValueError as e:
        assert str(e) == "slot_taken"

    # A different staff member CAN take the same time.
    ok = bookings.create(cid, {
        "service": "Haircut", "service_duration_min": 30, "staff": "Jacob",
        "date": FUTURE_MON, "start": "10:30", "customer_name": "C",
    })
    assert ok["staff"] == "Jacob"


def test_end_computed_and_update():
    cid = "t-end"
    _reset(cid)
    b = bookings.create(cid, {
        "service": "Fade", "service_duration_min": 45,
        "date": FUTURE_MON, "start": "12:00", "customer_name": "A",
    })
    assert b["end"] == "12:45"
    upd = bookings.update(cid, b["id"], {"start": "13:00"})
    assert upd["end"] == "13:45"  # recomputed from new start
    cancelled = bookings.cancel(cid, b["id"])
    assert cancelled["status"] == "cancelled"
    # Cancelled slot no longer blocks availability.
    assert bookings.is_slot_open(cid, FUTURE_MON, "13:00", 45)


def test_stats():
    cid = "t-stats"
    _reset(cid)
    bookings.create(cid, {"service": "X", "service_duration_min": 30,
                          "date": "2099-02-02", "start": "10:00", "customer_name": "A"})
    b = bookings.create(cid, {"service": "X", "service_duration_min": 30,
                              "date": "2099-02-02", "start": "11:00", "customer_name": "B"})
    bookings.set_status(cid, b["id"], "no_show")
    s = bookings.stats(cid)
    assert s["total"] == 2
    assert s["no_shows"] == 1
    assert s["upcoming"] == 1  # the no_show is not upcoming


def _run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"  ok  {fn.__name__}")
    print(f"\n{len(fns)} tests passed.")


if __name__ == "__main__":
    _run_all()
