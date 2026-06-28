"""Hermes tool: control LazusAI from Telegram.

Commands (sent to Hermes as `lazusai <subcommand> ...`):
  lazusai new "<business name>" <apple_id_number>   create a client
  lazusai status                                    list clients + today's counts
  lazusai leads <client_id>                         today's leads for a client
  lazusai pause <client_id>                          stop the bot for a client

All work goes through the LazusAI Core API; this module is a thin command
parser + HTTP client so it can be registered with the existing Hermes runtime.

Registration: import `handle(command_text) -> str` and wire it to the Hermes
command named "lazusai" (see register() for a generic adapter), or call
handle() directly. Returns a plain-text reply suitable for Telegram.
"""
from __future__ import annotations

import os
import shlex

import httpx

CORE = os.environ.get("CORE_API_URL", "http://127.0.0.1:8003").rstrip("/")
KEY = os.environ.get("LAZUSAI_CORE_KEY", "")
HDR = {"X-LazusAI-Key": KEY, "Content-Type": "application/json"}

USAGE = (
    "LazusAI commands:\n"
    '  lazusai new "<business name>" <apple_id_number>\n'
    "  lazusai status\n"
    "  lazusai leads <client_id>\n"
    "  lazusai pause <client_id>"
)


def handle(command_text: str) -> str:
    """Parse and execute a `lazusai ...` command, returning a text reply."""
    text = command_text.strip()
    if text.lower().startswith("lazusai"):
        text = text[len("lazusai"):].strip()
    try:
        parts = shlex.split(text)
    except ValueError:
        parts = text.split()
    if not parts:
        return USAGE
    sub, args = parts[0].lower(), parts[1:]

    try:
        if sub == "new":
            return _new(args)
        if sub == "status":
            return _status()
        if sub == "leads":
            return _leads(args)
        if sub == "pause":
            return _pause(args)
        return USAGE
    except httpx.HTTPStatusError as e:
        return f"⚠️ LazusAI API error: {e.response.status_code} {e.response.text[:200]}"
    except Exception as e:  # noqa: BLE001
        return f"⚠️ LazusAI error: {e}"


def _new(args: list[str]) -> str:
    if len(args) < 2:
        return 'Usage: lazusai new "<business name>" <apple_id_number>'
    apple_id = args[-1]
    business = " ".join(args[:-1])
    r = httpx.post(f"{CORE}/clients", headers=HDR, timeout=60,
                   json={"business_name": business, "apple_id_number": apple_id})
    r.raise_for_status()
    data = r.json()
    kv = "✅ live" if data.get("kv_synced") else "⚠️ run sync_clients_to_kv.sh"
    return (f"✅ Created *{business}*\n"
            f"client_id: `{data['client_id']}`\n"
            f"apple id: {apple_id}\nRouting: {kv}\n"
            f"Workflows are parameterized by client_id — no extra setup needed.")


def _status() -> str:
    r = httpx.get(f"{CORE}/clients", headers=HDR, timeout=30)
    r.raise_for_status()
    rows = r.json().get("clients", [])
    if not rows:
        return "No clients yet. Create one with: lazusai new \"<name>\" <apple_id>"
    lines = ["📋 LazusAI clients:"]
    for c in rows:
        state = "🟢" if c.get("active") else "⏸️"
        lines.append(
            f"{state} {c['business_name']} (`{c['client_id']}`) — "
            f"{c.get('messages_today', 0)} msgs, {c.get('leads_today', 0)} leads today"
        )
    return "\n".join(lines)


def _leads(args: list[str]) -> str:
    if not args:
        return "Usage: lazusai leads <client_id>"
    cid = args[0]
    r = httpx.get(f"{CORE}/clients/{cid}/leads", params={"today": "true"}, headers=HDR, timeout=30)
    r.raise_for_status()
    leads = r.json().get("leads", [])
    if not leads:
        return f"No leads today for `{cid}`."
    lines = [f"🎯 Today's leads for `{cid}` ({len(leads)}):"]
    for l in leads:
        who = l.get("name") or l.get("phone") or l.get("sender") or "unknown"
        at = (l.get("captured_at", "") or "")[11:16]
        lines.append(f"• {at} {who} — {(l.get('summary') or l.get('message') or '')[:80]}")
    return "\n".join(lines)


def _pause(args: list[str]) -> str:
    if not args:
        return "Usage: lazusai pause <client_id>"
    cid = args[0]
    r = httpx.post(f"{CORE}/clients/{cid}/toggle", params={"active": "false"}, headers=HDR, timeout=30)
    r.raise_for_status()
    return f"⏸️ Paused `{cid}`. The bot will stop responding until resumed."


def register(hermes):
    """Generic adapter: register `handle` as the Hermes `lazusai` command.

    The Hermes runtime API varies; this covers the common shapes. If yours
    differs, call handle(text) directly from your own command binding.
    """
    if hasattr(hermes, "register_command"):
        hermes.register_command("lazusai", lambda text, **_: handle(text))
    elif hasattr(hermes, "add_tool"):
        hermes.add_tool(name="lazusai", description=USAGE, run=handle)
    else:  # decorator/dict-style registries
        try:
            hermes["lazusai"] = handle
        except Exception:  # noqa: BLE001
            raise RuntimeError("Unknown Hermes API — call lazusai_tool.handle() directly.")


if __name__ == "__main__":
    import sys
    print(handle(" ".join(sys.argv[1:])))
