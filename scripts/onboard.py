#!/usr/bin/env python3
"""LazusAI interactive onboarding.

Walks the operator through the questions needed to stand up a new client, then
creates everything via the Core API (config JSON, ChromaDB collection, KV
routing). Workflows are already multi-tenant (parameterized by client_id), so no
per-client workflow needs creating — the new client is live the moment routing
is registered.

Usage:
  CORE_API_URL=https://core.lazusai.com LAZUSAI_CORE_KEY=... python scripts/onboard.py
"""
from __future__ import annotations

import os
import sys

import httpx

CORE = os.environ.get("CORE_API_URL", "http://127.0.0.1:8003").rstrip("/")
KEY = os.environ.get("LAZUSAI_CORE_KEY", "")
HDR = {"X-LazusAI-Key": KEY, "Content-Type": "application/json"}


def ask(prompt: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    val = input(f"{prompt}{suffix}: ").strip()
    return val or default


def ask_list(prompt: str) -> list[str]:
    print(f"{prompt} (one per line, blank to finish):")
    items = []
    while True:
        line = input("  - ").strip()
        if not line:
            break
        items.append(line)
    return items


def ask_pricing() -> dict:
    print("Pricing (format 'service = price', blank to finish):")
    pricing = {}
    while True:
        line = input("  - ").strip()
        if not line:
            break
        if "=" in line:
            k, v = line.split("=", 1)
            pricing[k.strip()] = v.strip()
    return pricing


def ask_hours() -> dict:
    print("Hours (e.g. 'mon = 08:00-17:00', blank to finish; use 'closed'):")
    hours = {}
    while True:
        line = input("  - ").strip()
        if not line:
            break
        if "=" in line:
            k, v = line.split("=", 1)
            hours[k.strip().lower()] = v.strip()
    return hours


def ask_faqs() -> list[dict]:
    print("Top FAQs (enter a question then its answer; blank question to finish):")
    faqs = []
    while len(faqs) < 10:
        q = input(f"  Q{len(faqs)+1}: ").strip()
        if not q:
            break
        a = input("      A: ").strip()
        faqs.append({"q": q, "a": a})
    return faqs


def ask_yn(prompt: str, default: bool = False) -> bool:
    d = "Y/n" if default else "y/N"
    val = input(f"{prompt} [{d}]: ").strip().lower()
    if not val:
        return default
    return val.startswith("y")


def ask_staff() -> list[dict]:
    print("Team members who take appointments (blank name to finish):")
    staff = []
    while True:
        name = input(f"  Name #{len(staff)+1}: ").strip()
        if not name:
            break
        phone = ask("      Their phone/iMessage (recognized as staff, gets alerts)")
        role = ask("      Role", "")
        svcs = input("      Services they do (comma separated, blank = all): ").strip()
        staff.append({
            "name": name,
            "phone": phone,
            "role": role,
            "services": [s.strip() for s in svcs.split(",") if s.strip()],
            "notify": True,
        })
    return staff


def ask_services_matrix() -> list[dict]:
    print("Bookable services (blank name to finish):")
    out = []
    while True:
        name = input(f"  Service #{len(out)+1}: ").strip()
        if not name:
            break
        price = input("      Price ($, number): ").strip()
        dur = input("      Duration (minutes) [30]: ").strip() or "30"
        staff = input("      Staff who do it (comma separated, blank = anyone): ").strip()
        deposit = input("      Deposit ($, blank = none): ").strip()
        svc = {"name": name, "duration_min": int(dur) if dur.isdigit() else 30,
               "staff": [s.strip() for s in staff.split(",") if s.strip()]}
        if price:
            try:
                svc["price"] = float(price)
            except ValueError:
                pass
        if deposit:
            try:
                svc["deposit"] = float(deposit)
            except ValueError:
                pass
        out.append(svc)
    return out


def ask_payment() -> dict:
    print("Payments — how does this business collect up front?")
    print("  1) Nothing (pay on-site)   2) Deposit   3) Full payment")
    choice = input("  Choose [1]: ").strip() or "1"
    model = {"1": "none", "2": "deposit", "3": "full"}.get(choice, "none")
    if model == "none":
        return {"processor": "none", "model": "none"}
    processor = (ask("  Processor (square/stripe)", "square") or "square").lower()
    pay = {"processor": processor, "model": model, "currency": "USD"}
    if model == "deposit":
        dep = input("  Default deposit ($): ").strip()
        try:
            pay["deposit_amount"] = float(dep)
        except ValueError:
            pay["deposit_amount"] = 0
    if processor == "square":
        pay["square_access_token"] = ask("  Square access token")
        pay["square_location_id"] = ask("  Square location ID")
        pay["square_env"] = ask("  Square env (production/sandbox)", "production")
    elif processor == "stripe":
        pay["stripe_secret_key"] = ask("  Stripe secret key")
    return pay


def main() -> int:
    print("=== LazusAI client onboarding ===\n")
    business_name = ask("Business name")
    if not business_name:
        print("Business name is required.")
        return 1
    industry = ask("Industry")
    apple_id = ask("Apple ID number linked to BlueBubbles (e.g. +15551234567)")
    owner_tg = ask("Owner Telegram handle or chat id")
    print()
    hours = ask_hours()
    print()
    services = ask_list("Services offered")
    print()
    pricing = ask_pricing()
    print()
    faqs = ask_faqs()
    print()
    personality = ask(
        "AI personality / tone",
        f"Friendly, concise front-desk assistant for {business_name}.",
    )
    print()

    # --- Booking (optional) ---
    booking_enabled = ask_yn("Should the bot take appointments for this client?", False)
    staff: list[dict] = []
    services_matrix: list[dict] = []
    slot_minutes = 30
    integrations: dict = {}
    if booking_enabled:
        print()
        staff = ask_staff()
        print()
        services_matrix = ask_services_matrix()
        print()
        sm = ask("Booking slot interval in minutes", "30")
        slot_minutes = int(sm) if sm.isdigit() else 30
        print()
        payment = ask_payment()
        if payment:
            integrations = {"payment": payment}

    payload = {
        "business_name": business_name,
        "industry": industry,
        "apple_id_number": apple_id,
        "owner_telegram": owner_tg,
        "hours": hours,
        "services": services,
        "pricing": pricing,
        "faqs": faqs,
        "ai_personality": personality,
        "booking_enabled": booking_enabled,
        "slot_minutes": slot_minutes,
        "staff": staff,
        "services_matrix": services_matrix,
        "integrations": integrations,
    }

    print("\nCreating client via Core API...")
    try:
        r = httpx.post(f"{CORE}/clients", headers=HDR, json=payload, timeout=120)
        r.raise_for_status()
    except Exception as e:  # noqa: BLE001
        print(f"❌ Failed: {e}")
        print("Saved nothing. Check CORE_API_URL / LAZUSAI_CORE_KEY and retry.")
        return 1

    data = r.json()
    cid = data["client_id"]
    print(f"\n✅ Client created: {cid}")
    print(f"   Config:    data/clients/{cid}.json")
    print(f"   ChromaDB:  collection client_{cid}")
    print(f"   Dashboard: https://lazusai.com/admin/{cid}")
    if data.get("kv_synced"):
        print("   Routing:   live in Cloudflare KV — client is receiving messages.")
    else:
        print("   Routing:   run `npm run sync-clients` to push routes to the Worker KV.")
    print("\nWorkflows are parameterized by client_id; no per-client workflow setup needed.")
    if payload["booking_enabled"]:
        print("   Booking:   enabled — manage the calendar, team & services from the dashboard.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
