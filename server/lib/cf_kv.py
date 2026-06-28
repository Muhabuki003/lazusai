"""Push client routing/config/auth into the Cloudflare Worker KV namespace.

The Worker reads three key families from CLIENTS_KV:
  route:<identifier> -> client_id   (apple_id_number, chat guid)
  client:<client_id> -> config JSON
  auth:<client_id>   -> "user:password" (dashboard Basic auth)

Best-effort: if Cloudflare credentials aren't configured, callers should fall
back to scripts/sync_clients_to_kv.sh. Requires env:
  CF_ACCOUNT_ID, CF_KV_NAMESPACE_ID, CF_API_TOKEN  (KV edit permission)
"""
from __future__ import annotations

import json
import os

import httpx

CF_ACCOUNT_ID = os.environ.get("CF_ACCOUNT_ID", "")
CF_KV_NAMESPACE_ID = os.environ.get("CF_KV_NAMESPACE_ID", "")
CF_API_TOKEN = os.environ.get("CF_API_TOKEN", "")


def configured() -> bool:
    return bool(CF_ACCOUNT_ID and CF_KV_NAMESPACE_ID and CF_API_TOKEN)


def _base() -> str:
    return (
        f"https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT_ID}"
        f"/storage/kv/namespaces/{CF_KV_NAMESPACE_ID}"
    )


def _put(key: str, value: str) -> None:
    httpx.put(
        f"{_base()}/values/{key}",
        headers={"Authorization": f"Bearer {CF_API_TOKEN}"},
        content=value,
        timeout=30,
    ).raise_for_status()


def push_client(config: dict, dashboard_password: str | None = None) -> bool:
    """Sync one client's routes, config and (optionally) auth into KV.

    Returns False (no-op) if Cloudflare creds aren't configured.
    """
    if not configured():
        return False
    cid = config["client_id"]
    for ident in (config.get("apple_id_number"), config.get("bluebubbles_chat_guid")):
        if ident:
            _put(f"route:{ident}", cid)
    _put(f"client:{cid}", json.dumps(config))
    if dashboard_password:
        user = config.get("dashboard_user", cid)
        _put(f"auth:{cid}", f"{user}:{dashboard_password}")
    return True
