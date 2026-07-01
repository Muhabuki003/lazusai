"""Per-client payment links. Square is the primary processor (most local
businesses already run Square tap-to-pay); Stripe is supported as a fallback.

Each client stores its own credentials in config["integrations"]["payment"]:

  {
    "processor":  "square" | "stripe" | "none",
    "model":      "none" | "deposit" | "full",
    "deposit_amount": 50,            # dollars, when model == "deposit"
    "currency":   "USD",
    "square_access_token": "...",     # per-client Square access token
    "square_location_id": "...",
    "square_env": "production",       # or "sandbox"
    "stripe_secret_key": "..."
  }

No credentials are ever hard-coded; if a client has no payment config the
booking flow simply skips payment (pay on-site). All calls are best-effort:
a failure returns (None, error_message) so a booking is never lost just
because a payment link couldn't be minted.
"""
from __future__ import annotations

import uuid

import httpx

SQUARE_BASE = {
    "production": "https://connect.squareup.com",
    "sandbox": "https://connect.squareupsandbox.com",
}
SQUARE_VERSION = "2024-10-17"
REQUEST_TIMEOUT = 20.0


def payment_config(client_cfg: dict) -> dict:
    return ((client_cfg or {}).get("integrations") or {}).get("payment") or {}


def amount_for(cfg_payment: dict, service_price) -> float:
    """How much to collect up-front given the client's payment model."""
    model = (cfg_payment.get("model") or "none").lower()
    if model == "full":
        return float(service_price or 0)
    if model == "deposit":
        return float(cfg_payment.get("deposit_amount") or 0)
    return 0.0


def is_enabled(client_cfg: dict) -> bool:
    p = payment_config(client_cfg)
    return (p.get("processor") or "none").lower() in ("square", "stripe") \
        and (p.get("model") or "none").lower() in ("deposit", "full")


def create_payment_link(
    client_cfg: dict,
    *,
    amount: float,
    description: str,
    reference_id: str = "",
    buyer_phone: str = "",
    http_client: httpx.Client | None = None,
) -> tuple[str | None, str | None]:
    """Return (url, None) on success or (None, error) on failure.

    `amount` is in dollars; it is converted to the processor's minor units.
    """
    p = payment_config(client_cfg)
    processor = (p.get("processor") or "none").lower()
    if amount <= 0:
        return None, "zero_amount"
    if processor == "square":
        return _square_link(p, amount, description, reference_id, buyer_phone, http_client)
    if processor == "stripe":
        return _stripe_link(p, amount, description, reference_id, http_client)
    return None, "no_processor"


# ------------------------------------------------------------------- square
def _square_link(p, amount, description, reference_id, buyer_phone, http_client):
    token = p.get("square_access_token")
    location_id = p.get("square_location_id")
    if not token or not location_id:
        return None, "square_not_configured"
    base = SQUARE_BASE.get((p.get("square_env") or "production").lower(), SQUARE_BASE["production"])
    currency = (p.get("currency") or "USD").upper()
    body = {
        "idempotency_key": uuid.uuid4().hex,
        "quick_pay": {
            "name": description[:255] or "Booking",
            "price_money": {"amount": int(round(amount * 100)), "currency": currency},
            "location_id": location_id,
        },
    }
    if reference_id:
        body["payment_note"] = reference_id[:500]
    if buyer_phone:
        body["pre_populated_data"] = {"buyer_phone_number": buyer_phone}
    headers = {
        "Square-Version": SQUARE_VERSION,
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    try:
        url_ = f"{base}/v2/online-checkout/payment-links"
        if http_client is not None:
            resp = http_client.post(url_, headers=headers, json=body)
        else:
            with httpx.Client(timeout=REQUEST_TIMEOUT) as c:
                resp = c.post(url_, headers=headers, json=body)
        data = resp.json()
        if resp.status_code >= 300:
            errs = data.get("errors") or [{"detail": f"HTTP {resp.status_code}"}]
            return None, "square: " + "; ".join(e.get("detail", "error") for e in errs)
        link = (data.get("payment_link") or {}).get("url")
        return (link, None) if link else (None, "square: no url in response")
    except Exception as exc:  # noqa: BLE001
        return None, f"square: {exc}"


# ------------------------------------------------------------------- stripe
def _stripe_link(p, amount, description, reference_id, http_client):
    key = p.get("stripe_secret_key")
    if not key:
        return None, "stripe_not_configured"
    currency = (p.get("currency") or "USD").lower()
    # Stripe Payment Links need a price; use inline price_data via the
    # form-encoded API. One-off link tied to an ad-hoc product.
    form = {
        "line_items[0][price_data][currency]": currency,
        "line_items[0][price_data][product_data][name]": description[:250] or "Booking",
        "line_items[0][price_data][unit_amount]": str(int(round(amount * 100))),
        "line_items[0][quantity]": "1",
    }
    if reference_id:
        form["metadata[reference_id]"] = reference_id
    headers = {"Authorization": f"Bearer {key}",
               "Content-Type": "application/x-www-form-urlencoded"}
    try:
        url_ = "https://api.stripe.com/v1/payment_links"
        if http_client is not None:
            resp = http_client.post(url_, headers=headers, data=form)
        else:
            with httpx.Client(timeout=REQUEST_TIMEOUT) as c:
                resp = c.post(url_, headers=headers, data=form)
        data = resp.json()
        if resp.status_code >= 300:
            return None, "stripe: " + (data.get("error", {}).get("message") or f"HTTP {resp.status_code}")
        return (data.get("url"), None) if data.get("url") else (None, "stripe: no url")
    except Exception as exc:  # noqa: BLE001
        return None, f"stripe: {exc}"
