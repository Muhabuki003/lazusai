# LazusAI

Multi-tenant iMessage AI chatbot platform for local service businesses.

LazusAI lets a single operator run AI receptionists for many client businesses
over iMessage. Each client gets isolated config, an isolated ChromaDB knowledge
base, isolated leads, and its own n8n workflow instance — all keyed by
`apple_id_number` + `client_id`. Every piece runs on the existing self-hosted
stack; there are **no external paid AI services**. All inference routes through
the local NVIDIA NIM stack (DeepSeek primary, Kimi → GLM 5.2 → Mistral
fallback).

```
 iMessage ─▶ BlueBubbles (home Mac) ─▶ Cloudflare Tunnel
                                          │  webhook
                                          ▼
                        Cloudflare Worker  (lazusai.com/webhook)
                          · identify client by phone / chat guid
                          · flag voice notes (.caf / .m4a)
                          · route to per-client n8n workflow
                                          │
                                          ▼
                        n8n  (n8n.bookistudios.com)
                          WF1 Inbound Handler  ──┐
                          WF2 Owner Alert        │  Hetzner VPS
                          WF3 Daily Summary      │  167.233.38.96
                                          │      │
                  ┌───────────────────────┼──────┴───────────────┐
                  ▼            ▼           ▼          ▼            ▼
              Whisper      NIM stack   ChromaDB   BlueBubbles   Telegram
              :8002        :8000       :8001      REST API      (owner)
```

## Repository layout

| Path | What it is | Runs on |
|------|------------|---------|
| `src/worker.js` | BlueBubbles webhook receiver + admin router | Cloudflare Workers |
| `wrangler.toml` | Worker config, KV + routes | — |
| `data/clients/` | Per-client config JSON + lookup index | source of truth (synced to KV) |
| `data/leads/` | Captured leads per client | Hetzner VPS |
| `n8n/` | Importable workflow JSON (WF1/WF2/WF3) | n8n |
| `server/whisper_service/` | Whisper transcription REST API (port 8002) | Hetzner VPS |
| `server/lib/` | Shared Python: NIM client, ChromaDB, client store | Hetzner VPS |
| `server/hermes/` | Hermes `lazusai` tool (Telegram control) | Hetzner VPS |
| `admin/dashboard.html` | Single-file cinematic admin dashboard | served by Worker |
| `scripts/onboard.py` | Interactive client onboarding | operator machine / VPS |
| `scripts/sync_clients_to_kv.sh` | Push `data/clients/` → Worker KV | operator machine |
| `docs/DEPLOYMENT.md` | Full deployment runbook | — |

## Quick start

See [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md) for the full runbook. Short
version:

```bash
# 1. Worker
npm install
npx wrangler kv namespace create CLIENTS_KV
# put the returned id in wrangler.toml, then:
npx wrangler deploy

# 2. Whisper service on Hetzner
cd server/whisper_service && ./install.sh

# 3. Import n8n/*.json into n8n.bookistudios.com

# 4. Onboard the first client
python scripts/onboard.py
```

## Multi-tenancy

Isolation is enforced at every layer:

- **Routing** — Worker maps inbound `apple_id_number` / `chat_guid` / sender
  phone to exactly one `client_id`. Unknown numbers are dropped.
- **Config** — one `data/clients/<client_id>.json` per tenant.
- **Knowledge** — one ChromaDB collection `client_<client_id>` per tenant.
- **Leads** — `data/leads/<client_id>/leads.json` per tenant.
- **Workflows** — each n8n workflow is parameterized by `client_id`; a tenant's
  prompt, personality, and context never cross into another's.
