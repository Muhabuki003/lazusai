"""End-to-end tests for the Core API booking endpoints.

ChromaDB and the NIM client are stubbed so the suite runs without the heavy
vector-DB / model dependencies. Payment + notification side-effects are
monkeypatched to avoid network. Run:

    cd server && python tests/test_core_api.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import types
from pathlib import Path

# --- isolate data dir + stub heavy imports BEFORE importing the app ----------
_TMP = tempfile.mkdtemp(prefix="lazusai-api-")
os.environ["LAZUSAI_DATA_DIR"] = _TMP
os.environ.pop("LAZUSAI_CORE_KEY", None)  # disable auth for the test
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Fake chromadb so `import chromadb` in lib/chroma_store succeeds.
_fake_chroma = types.ModuleType("chromadb")
class _FakeCol:
    def __init__(self): self._d = {}
    def upsert(self, ids, documents=None, metadatas=None):
        for i in ids: self._d[i] = True
    def add(self, ids, documents=None, metadatas=None):
        for i in ids: self._d[i] = True
    def get(self, where=None, include=None): return {"ids": [], "documents": [], "metadatas": []}
    def query(self, **k): return {"documents": [[]]}
    def delete(self, ids=None): pass
class _FakeConn:
    def get_or_create_collection(self, **k): return _FakeCol()
_fake_chroma.HttpClient = lambda **k: _FakeConn()
_fake_chroma.api = types.SimpleNamespace(ClientAPI=object)
sys.modules["chromadb"] = _fake_chroma

# Seed a booking-enabled client config on disk.
CLIENTS = Path(_TMP) / "clients"
CLIENTS.mkdir(parents=True, exist_ok=True)
BARBERS = {
    "client_id": "acme-barbers", "business_name": "Acme Barbers",
    "industry": "Barbershop", "apple_id_number": "+15555550110",
    "bluebubbles_chat_guid": "iMessage;-;+15555550110", "owner_telegram": "@owner",
    "hours": {"mon": "10:00-19:00", "tue": "10:00-19:00", "wed": "10:00-19:00",
              "thu": "10:00-19:00", "fri": "10:00-19:00", "sat": "10:00-14:00", "sun": "closed"},
    "booking_enabled": True, "slot_minutes": 30,
    "services_matrix": [
        {"name": "Haircut", "price": 35, "duration_min": 30, "staff": ["Josh", "Jacob"], "deposit": 10},
        {"name": "Skin Fade", "price": 40, "duration_min": 45, "staff": ["Josh"]},
    ],
    "staff": [
        {"name": "Josh", "phone": "+15555550111", "role": "Master Barber",
         "services": ["Haircut", "Skin Fade"], "notify": True},
        {"name": "Jacob", "phone": "+15555550112", "role": "Barber",
         "services": ["Haircut"], "notify": True},
    ],
    "integrations": {"payment": {"processor": "square", "model": "deposit",
                                 "deposit_amount": 10, "square_access_token": "tok",
                                 "square_location_id": "LOC", "square_env": "sandbox"}},
    "active": True, "created_at": "2026-06-28T00:00:00Z",
}
(CLIENTS / "acme-barbers.json").write_text(json.dumps(BARBERS))

from fastapi.testclient import TestClient  # noqa: E402
from core_api import app as core_app  # noqa: E402
from lib import payments, notify  # noqa: E402

# Stub payment link + notifications (no network), undone after this module
# so test_payments.py exercises the real implementations.
import pytest  # noqa: E402


@pytest.fixture(autouse=True, scope="module")
def _stub_integrations():
    mp = pytest.MonkeyPatch()
    mp.setattr(payments, "create_payment_link",
               lambda *a, **k: ("https://square.link/u/test", None))
    mp.setattr(notify, "notify_staff_of_booking",
               lambda cfg, b, **k: {"staff": [b.get("staff")], "owner": True})
    yield
    mp.undo()


client = TestClient(core_app.app)
FUTURE_MON = "2099-01-05"  # a Monday


def test_availability_lists_slots():
    r = client.get("/clients/acme-barbers/availability",
                   params={"date": FUTURE_MON, "service": "Haircut"})
    assert r.status_code == 200
    data = r.json()
    assert data["duration_min"] == 30
    starts = {s["start"] for s in data["slots"]}
    assert "10:00" in starts
    # Both barbers offer Haircut → 10:00 should be available for each.
    at_10 = {s["staff"] for s in data["slots"] if s["start"] == "10:00"}
    assert at_10 == {"Josh", "Jacob"}


def test_skin_fade_only_josh():
    r = client.get("/clients/acme-barbers/availability",
                   params={"date": FUTURE_MON, "service": "Skin Fade"})
    slots = r.json()["slots"]
    assert slots and all(s["staff"] == "Josh" for s in slots)


def test_create_booking_with_deposit_and_alert():
    r = client.post("/clients/acme-barbers/bookings", json={
        "service": "Haircut", "staff": "Josh", "date": FUTURE_MON, "start": "11:00",
        "customer_name": "Jacob C", "customer_phone": "+15551239999",
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["booking"]["service"] == "Haircut"
    assert body["booking"]["service_price"] == 35
    assert body["booking"]["service_duration_min"] == 30
    # Deposit resolved from per-service override ($10) and payment link attached.
    assert body["payment"]["required"] is True
    assert body["payment"]["amount"] == 10
    assert body["booking"]["payment_link"] == "https://square.link/u/test"
    assert body["booking"]["payment_status"] == "deposit_pending"
    assert body["alerts"]["owner"] is True


def test_double_booking_conflict():
    # Josh already booked 11:00-11:30 above; booking him again overlaps.
    r = client.post("/clients/acme-barbers/bookings", json={
        "service": "Haircut", "staff": "Josh", "date": FUTURE_MON, "start": "11:00",
        "customer_name": "Someone",
    })
    assert r.status_code == 409
    assert r.json()["detail"] == "slot_taken"


def test_availability_reflects_booking():
    r = client.get("/clients/acme-barbers/availability",
                   params={"date": FUTURE_MON, "service": "Haircut", "staff": "Josh"})
    starts = {s["start"] for s in r.json()["slots"]}
    assert "11:00" not in starts  # now taken


def test_identify_staff_vs_customer():
    staff = client.get("/clients/acme-barbers/identify", params={"phone": "+1 (555) 555-0111"})
    assert staff.json()["role"] == "staff"
    assert staff.json()["name"] == "Josh"
    cust = client.get("/clients/acme-barbers/identify", params={"phone": "+15550009999"})
    assert cust.json()["role"] == "customer"


def test_list_and_cancel():
    lst = client.get("/clients/acme-barbers/bookings", params={"date": FUTURE_MON})
    bks = lst.json()["bookings"]
    assert len(bks) == 1
    bid = bks[0]["id"]
    c = client.post(f"/clients/acme-barbers/bookings/{bid}/cancel")
    assert c.json()["booking"]["status"] == "cancelled"
    # Slot frees up again.
    av = client.get("/clients/acme-barbers/availability",
                    params={"date": FUTURE_MON, "service": "Haircut", "staff": "Josh"})
    assert "11:00" in {s["start"] for s in av.json()["slots"]}


def test_booking_disabled_client():
    (CLIENTS / "noboo.json").write_text(json.dumps({
        "client_id": "noboo", "business_name": "No Booking", "industry": "x",
        "apple_id_number": "+15550000001", "bluebubbles_chat_guid": "x",
        "owner_telegram": "", "booking_enabled": False, "active": True,
        "created_at": "2026-06-28T00:00:00Z",
    }))
    r = client.post("/clients/noboo/bookings", json={
        "service": "X", "date": FUTURE_MON, "start": "10:00"})
    assert r.status_code == 400
    assert r.json()["detail"] == "booking_disabled"


def _run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    # Order matters (state builds up): run in source order, not alphabetical.
    order = ["test_availability_lists_slots", "test_skin_fade_only_josh",
             "test_create_booking_with_deposit_and_alert", "test_double_booking_conflict",
             "test_availability_reflects_booking", "test_identify_staff_vs_customer",
             "test_list_and_cancel", "test_booking_disabled_client"]
    for name in order:
        globals()[name]()
        print(f"  ok  {name}")
    print(f"\n{len(order)} tests passed.")


if __name__ == "__main__":
    _run_all()
