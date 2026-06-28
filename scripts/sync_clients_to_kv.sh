#!/usr/bin/env bash
# Push data/clients/*.json into the Worker's CLIENTS_KV namespace using wrangler.
# Writes route:<id> -> client_id, client:<id> -> config, and (optionally)
# auth:<id> -> "user:password" when LAZUSAI_<CID>_PASS env vars are present.
#
# Requires: wrangler authenticated, jq, and the KV binding "CLIENTS_KV" in
# wrangler.toml. Run from the repo root: npm run sync-clients
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."
BINDING="CLIENTS_KV"

command -v jq >/dev/null || { echo "jq is required"; exit 1; }

put() { # key value
  npx wrangler kv key put --binding "$BINDING" "$1" "$2" >/dev/null
  echo "  put $1"
}

for f in data/clients/*.json; do
  base="$(basename "$f")"
  case "$base" in _schema.json|index.json) continue;; esac

  cid="$(jq -r '.client_id' "$f")"
  echo "==> $cid"

  apple="$(jq -r '.apple_id_number // empty' "$f")"
  guid="$(jq -r '.bluebubbles_chat_guid // empty' "$f")"
  [ -n "$apple" ] && put "route:$apple" "$cid"
  [ -n "$guid" ]  && put "route:$guid" "$cid"

  put "client:$cid" "$(jq -c . "$f")"

  # Dashboard credentials: looks for env LAZUSAI_<CID_UPPER>_PASS.
  user="$(jq -r '.dashboard_user // .client_id' "$f")"
  var="LAZUSAI_$(echo "$cid" | tr 'a-z-' 'A-Z_')_PASS"
  pass="${!var:-}"
  if [ -n "$pass" ]; then
    put "auth:$cid" "$user:$pass"
  else
    echo "  (skip auth: set $var to enable dashboard login)"
  fi
done

echo "Done. Verify: npx wrangler kv key list --binding $BINDING"
