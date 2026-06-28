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
    return 0


if __name__ == "__main__":
    sys.exit(main())
