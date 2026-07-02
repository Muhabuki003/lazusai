"""Tests for the pending-signup queue: create -> (optionally mark-paid) ->
approve/reject, and that approval provisions a tenant using the wizard's
freeform notes without any manual re-entry."""
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _fake_chromadb():
    fake = types.ModuleType("chromadb")

    class _Col(dict):
        def add(self, **kw): pass
        def get(self, **kw): return {"documents": [], "metadatas": [], "ids": []}
        def query(self, **kw): return {"documents": [[]], "metadatas": [[]]}
        def delete(self, **kw): pass

    class _Client:
        def __init__(self, *a, **kw): pass
        def get_or_create_collection(self, *a, **kw): return _Col()

    fake.HttpClient = _Client
    return fake


def _app(tmp_path, monkeypatch):
    monkeypatch.setenv("LAZUSAI_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("LAZUSAI_CORE_KEY", "")
    monkeypatch.setitem(sys.modules, "chromadb", _fake_chromadb())
    import importlib
    from lib import clients as clients_mod
    importlib.reload(clients_mod)
    from core_api import app as core_app
    importlib.reload(core_app)
    from fastapi.testclient import TestClient
    return TestClient(core_app.app), core_app


def test_signup_create_list_approve(tmp_path, monkeypatch):
    client, core_app = _app(tmp_path, monkeypatch)

    r = client.post("/signups", json={
        "name": "Jane Doe", "email": "jane@acme.com", "phone": "+15551230000",
        "business": "Acme Roofing", "industry": "Roofing",
        "services": "Roof repair from $300, full replacement from $8000",
        "hours": "Mon-Fri 8-5", "faqs": "Do you offer financing? Yes.",
        "plan": "starter",
    })
    assert r.status_code == 200
    signup_id = r.json()["id"]

    listed = client.get("/signups", params={"status": "pending"}).json()["signups"]
    assert len(listed) == 1 and listed[0]["business"] == "Acme Roofing"

    approve = client.post(f"/signups/{signup_id}/approve")
    assert approve.status_code == 200
    body = approve.json()
    cid = body["client_id"]
    assert body["config"]["active"] is False  # no phone number yet
    assert "Roof repair from $300" in body["config"]["raw_intake"]["services"]

    pending_after = client.get("/signups", params={"status": "pending"}).json()["signups"]
    assert pending_after == []
    approved = client.get("/signups", params={"status": "approved"}).json()["signups"]
    assert approved[0]["client_id"] == cid

    # The provisioned client actually exists and carries the notes forward
    # into the prompt the AI will use.
    cfg = client.get(f"/clients/{cid}/config").json()
    assert cfg["business_name"] == "Acme Roofing"
    from lib import inbound
    prompt = inbound.build_customer_prompt(cfg, "+15550009999", "2099-01-05")
    assert "Roof repair from $300" in prompt
    assert "financing" in prompt.lower()


def test_signup_reject(tmp_path, monkeypatch):
    client, _ = _app(tmp_path, monkeypatch)
    signup_id = client.post("/signups", json={"business": "Bad Fit Co", "name": "X", "email": "x@x.com"}).json()["id"]
    r = client.post(f"/signups/{signup_id}/reject")
    assert r.status_code == 200
    assert client.get("/signups", params={"status": "rejected"}).json()["signups"][0]["business"] == "Bad Fit Co"
    # Can't double-approve a rejected signup.
    assert client.post(f"/signups/{signup_id}/approve").status_code == 400


def test_signup_mark_paid_does_not_auto_provision(tmp_path, monkeypatch):
    client, _ = _app(tmp_path, monkeypatch)
    signup_id = client.post("/signups", json={"business": "Paid Co", "name": "X", "email": "x@x.com", "plan": "pro"}).json()["id"]

    r = client.post(f"/signups/{signup_id}/mark-paid", json={
        "stripe_customer_id": "cus_123", "stripe_subscription_id": "sub_456",
    })
    assert r.status_code == 200

    pending = client.get("/signups", params={"status": "pending"}).json()["signups"]
    assert len(pending) == 1
    assert pending[0]["paid"] is True
    assert pending[0]["stripe_customer_id"] == "cus_123"


def test_approve_unknown_signup_404(tmp_path, monkeypatch):
    client, _ = _app(tmp_path, monkeypatch)
    assert client.post("/signups/nope/approve").status_code == 404
    assert client.post("/signups/nope/reject").status_code == 404
    assert client.post("/signups/nope/mark-paid", json={}).status_code == 404
