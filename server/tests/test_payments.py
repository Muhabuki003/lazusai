"""Tests for lib/payments with a mocked httpx client (no network)."""
from __future__ import annotations

import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lib import payments  # noqa: E402


def _mock_client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_amount_for_models():
    assert payments.amount_for({"model": "full"}, 120) == 120.0
    assert payments.amount_for({"model": "deposit", "deposit_amount": 50}, 120) == 50.0
    assert payments.amount_for({"model": "none"}, 120) == 0.0


def test_is_enabled():
    assert payments.is_enabled({"integrations": {"payment": {"processor": "square", "model": "deposit"}}})
    assert not payments.is_enabled({"integrations": {"payment": {"processor": "none", "model": "full"}}})
    assert not payments.is_enabled({})


def test_square_link_success():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["auth"] = request.headers.get("Authorization")
        import json
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"payment_link": {"url": "https://square.link/u/abc123"}})

    cfg = {"integrations": {"payment": {
        "processor": "square", "model": "deposit", "deposit_amount": 50,
        "square_access_token": "tok_test", "square_location_id": "LOC1",
        "square_env": "sandbox", "currency": "USD",
    }}}
    with _mock_client(handler) as c:
        url, err = payments.create_payment_link(
            cfg, amount=payments.amount_for(payments.payment_config(cfg), 120),
            description="Deposit — Haircut", reference_id="bk_1",
            buyer_phone="+15551234567", http_client=c,
        )
    assert err is None
    assert url == "https://square.link/u/abc123"
    assert "squareupsandbox.com" in captured["url"]
    assert captured["auth"] == "Bearer tok_test"
    # $50 -> 5000 cents
    assert captured["body"]["quick_pay"]["price_money"]["amount"] == 5000
    assert captured["body"]["quick_pay"]["location_id"] == "LOC1"


def test_square_error_surfaced():
    def handler(request):
        return httpx.Response(401, json={"errors": [{"detail": "Unauthorized"}]})

    cfg = {"integrations": {"payment": {
        "processor": "square", "model": "full",
        "square_access_token": "bad", "square_location_id": "LOC1",
    }}}
    with _mock_client(handler) as c:
        url, err = payments.create_payment_link(cfg, amount=120, description="x", http_client=c)
    assert url is None
    assert "Unauthorized" in err


def test_missing_credentials():
    cfg = {"integrations": {"payment": {"processor": "square", "model": "full"}}}
    url, err = payments.create_payment_link(cfg, amount=120, description="x")
    assert url is None
    assert err == "square_not_configured"


def test_zero_amount_skips():
    cfg = {"integrations": {"payment": {"processor": "square", "model": "none"}}}
    url, err = payments.create_payment_link(cfg, amount=0, description="x")
    assert url is None
    assert err == "zero_amount"


def test_stripe_link_success():
    def handler(request):
        assert "stripe.com" in str(request.url)
        return httpx.Response(200, json={"url": "https://buy.stripe.com/test_abc"})

    cfg = {"integrations": {"payment": {
        "processor": "stripe", "model": "full", "stripe_secret_key": "sk_test",
    }}}
    with _mock_client(handler) as c:
        url, err = payments.create_payment_link(cfg, amount=99.5, description="Full — Massage", http_client=c)
    assert err is None
    assert url == "https://buy.stripe.com/test_abc"


def _run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"  ok  {fn.__name__}")
    print(f"\n{len(fns)} tests passed.")


if __name__ == "__main__":
    _run_all()
