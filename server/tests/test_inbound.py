"""Unit tests for lib/inbound.py (webhook parsing, routing, prompts, directives,
lead detection) plus an end-to-end /webhook test against the FastAPI app with
the LLM and BlueBubbles stubbed out."""
import json
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib import inbound  # noqa: E402


# ------------------------------------------------------------------- parsing

def bb_event(**overrides):
    msg = {
        "guid": "msg-guid-1",
        "text": "Hi, do you do emergency plumbing?",
        "isFromMe": False,
        "handle": {"address": "+15551230000"},
        "chats": [{"guid": "iMessage;-;+15551234567"}],
        "attachments": [],
    }
    msg.update(overrides)
    return {"type": "new-message", "data": msg}


def test_parse_normal_message():
    p = inbound.parse_webhook(bb_event())
    assert p["sender"] == "+15551230000"
    assert p["chat_guid"] == "iMessage;-;+15551234567"
    assert p["text"].startswith("Hi, do you do")
    assert p["voice_note"] is False


def test_parse_ignores_from_me():
    p = inbound.parse_webhook(bb_event(isFromMe=True))
    assert p["ignored"] == "from_me"


def test_parse_ignores_other_event_types():
    ev = bb_event()
    ev["type"] = "typing-indicator"
    assert inbound.parse_webhook(ev)["ignored"] == "typing-indicator"


def test_parse_snake_case_shape():
    ev = {"type": "new_message", "data": {
        "is_from_me": False, "sender": "+15559998888",
        "chat_guid": "iMessage;-;+15551234567", "body": "hello",
    }}
    p = inbound.parse_webhook(ev)
    assert p["sender"] == "+15559998888"
    assert p["text"] == "hello"


def test_parse_voice_note_detection():
    p = inbound.parse_webhook(bb_event(attachments=[
        {"transferName": "Audio Message.caf", "mimeType": "audio/x-caf", "guid": "att-1"},
    ]))
    assert p["voice_note"] is True
    assert p["attachments"][0]["url"] == "/api/v1/attachment/att-1/download"


def test_parse_no_sender_rejected():
    ev = {"type": "new-message", "data": {"text": "hi", "isFromMe": False}}
    assert inbound.parse_webhook(ev)["ignored"] == "no_sender"


# ------------------------------------------------------------------- routing

ROUTES = {"iMessage;-;+15551234567": "acme", "+15551234567": "acme"}


def test_route_by_chat_guid():
    p = {"chat_guid": "iMessage;-;+15551234567", "sender": "+15550000000"}
    assert inbound.route_client(p, ROUTES) == "acme"


def test_route_unknown_returns_none():
    p = {"chat_guid": "iMessage;-;+19999999999", "sender": "+19999999999"}
    assert inbound.route_client(p, ROUTES) is None


def test_route_normalizes_bare_us_number():
    routes = {"+15551234567": "acme"}
    p = {"chat_guid": "", "sender": "5551234567"}
    assert inbound.route_client(p, routes) == "acme"


# ---------------------------------------------------------------- directives

def test_parse_directive_avail():
    d = inbound.parse_directive('Sure!\n[[AVAIL service="Haircut" date="2099-01-05" staff=""]]')
    assert d["verb"] == "AVAIL"
    assert d["args"]["service"] == "Haircut"
    assert d["args"]["date"] == "2099-01-05"


def test_strip_directive():
    out = inbound.strip_directive('Here are times.\n[[AVAIL service="x" date="y" staff=""]]')
    assert "[[" not in out and out.startswith("Here are times.")


# ------------------------------------------------------------------ leads

def test_detect_lead_name_and_phone():
    lead = inbound.detect_lead("My name is Mike Rodgers, call me at 555-123-4567", "+15550001111")
    assert lead["name"] == "Mike Rodgers"
    assert "555" in lead["phone"]


def test_detect_lead_none_for_smalltalk():
    assert inbound.detect_lead("what are your hours?", "+15550001111") is None


def test_escalated_keyword():
    cfg = {"escalation_keywords": ["urgent", "flooding"]}
    assert inbound.escalated(cfg, "my basement is FLOODING") is True
    assert inbound.escalated(cfg, "how much for a faucet?") is False


# ------------------------------------------------------------------ prompts

def test_customer_prompt_includes_business_context():
    cfg = {"business_name": "Acme Plumbing", "industry": "plumbing",
           "ai_personality": "friendly", "services": ["Drain cleaning"],
           "pricing": {"Drain cleaning": "$120"}, "hours": {"mon": "9-5"},
           "faqs": [{"q": "Emergency?", "a": "Yes 24/7."}]}
    p = inbound.build_customer_prompt(cfg, "+15550001111", "2099-01-05")
    for needle in ("Acme Plumbing", "Drain cleaning", "$120", "Emergency?"):
        assert needle in p


def test_booking_block_only_when_enabled():
    cfg = {"business_name": "X", "industry": "y", "ai_personality": "z"}
    assert "BOOKING IS ENABLED" not in inbound.build_customer_prompt(cfg, "s", "2099-01-05")
    cfg["booking_enabled"] = True
    cfg["services_matrix"] = [{"name": "Haircut", "price": 35, "duration_min": 30}]
    p = inbound.build_customer_prompt(cfg, "+15550001111", "2099-01-05")
    assert "BOOKING IS ENABLED" in p and "[[AVAIL" in p and "+15550001111" in p


# ----------------------------------------------------- end-to-end /webhook

def test_webhook_end_to_end(tmp_path, monkeypatch):
    # Isolated data dir with one routable client.
    monkeypatch.setenv("LAZUSAI_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("LAZUSAI_CORE_KEY", "")

    # Fake chromadb before importing the app (mirrors test_core_api.py).
    _fake = types.ModuleType("chromadb")

    class _Col(dict):
        def add(self, **kw): pass
        def get(self, **kw): return {"documents": [], "metadatas": [], "ids": []}
        def query(self, **kw): return {"documents": [[]], "metadatas": [[]]}
        def delete(self, **kw): pass

    class _Client:
        def __init__(self, *a, **kw): pass
        def get_or_create_collection(self, *a, **kw): return _Col()

    _fake.HttpClient = _Client
    monkeypatch.setitem(sys.modules, "chromadb", _fake)

    import importlib
    from lib import clients as clients_mod
    importlib.reload(clients_mod)
    (tmp_path / "clients").mkdir()
    (tmp_path / "clients" / "acme.json").write_text(json.dumps({
        "client_id": "acme", "business_name": "Acme Plumbing",
        "industry": "plumbing", "apple_id_number": "+15551234567",
        "bluebubbles_chat_guid": "iMessage;-;+15551234567",
        "ai_personality": "friendly", "active": True,
        "escalation_keywords": [], "services": ["Drains"],
    }))

    from core_api import app as core_app
    importlib.reload(core_app)

    from fastapi.testclient import TestClient
    client = TestClient(core_app.app)

    replies = {}
    monkeypatch.setattr(core_app.nim_client, "chat",
                        lambda msgs, **kw: types.SimpleNamespace(
                            text="We sure do! Want me to book a visit?", model="stub"))
    monkeypatch.setattr(core_app.inbound, "send_imessage_reply",
                        lambda guid, msg: replies.setdefault("sent", (guid, msg)) or True)
    logged = []
    monkeypatch.setattr(core_app.chroma_store, "log_turn",
                        lambda cid, role, text, sender="": logged.append(
                            (cid, {"role": role, "text": text, "sender": sender})))
    monkeypatch.setattr(core_app.chroma_store, "recent_turns", lambda cid, limit=10: [])

    r = client.post("/webhook", json=bb_event())
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True and body["client_id"] == "acme"

    # Background task runs before TestClient returns; verify pipeline effects.
    assert replies["sent"][0] == "iMessage;-;+15551234567"
    assert "book" in replies["sent"][1].lower()
    assert [t["role"] for _, t in logged] == ["user", "assistant"]

    # Unknown sender is dropped without processing.
    ev = bb_event()
    ev["data"]["handle"] = {"address": "+19998887777"}
    ev["data"]["chats"] = [{"guid": "iMessage;-;+19998887777"}]
    r2 = client.post("/webhook", json=ev)
    assert r2.json()["ignored"] == "unknown_client"
