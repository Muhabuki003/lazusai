# LazusAI Deployment Runbook

Everything runs on the existing stack. No new paid services. Order matters:
stand up the VPS services first, then n8n, then the Worker, then onboard.

## 0. Prerequisites

- **Hetzner VPS** `167.233.38.96` running: NVIDIA NIM (`:8000`), ChromaDB
  (`:8001`), n8n, Hermes. We add Whisper (`:8002`) and Core API (`:8003`).
- **BlueBubbles** on the home Mac, reachable via Cloudflare Tunnel.
- **Cloudflare** account with the `lazusai.com` zone and `wrangler` installed.
- A single shared secret used as `LAZUSAI_CORE_KEY` / `N8N_API_KEY` across the
  Worker, n8n, and Core API.

## 1. ChromaDB

Ensure ChromaDB is reachable at `127.0.0.1:8001` (its default container). Each
client gets its own collection `client_<client_id>`, created automatically.

## 2. Whisper service (`:8002`)

```bash
cd server/whisper_service
sudo ./install.sh
# optional: echo 'WHISPER_MODEL=small' | sudo tee -a /etc/lazusai/whisper.env
curl localhost:8002/health
```

Set `BLUEBUBBLES_URL` and `BLUEBUBBLES_PASSWORD` in
`/etc/lazusai/whisper.env` so it can fetch attachment audio from BlueBubbles.

## 3. Core API (`:8003`)

```bash
cd server/core_api
sudo ./install.sh
sudo nano /etc/lazusai/core.env   # set LAZUSAI_CORE_KEY, NIM_BASE_URL, etc.
sudo systemctl restart lazusai-core
curl localhost:8003/health
```

Expose the Core API to the Worker through the **Cloudflare Tunnel** as
`core.lazusai.com` (point the tunnel ingress at `http://127.0.0.1:8003`). The
Worker reaches it via `CORE_API_URL`.

## 4. n8n workflows

Import the files in `n8n/` into `n8n.bookistudios.com`
(Workflows → Import from File):

- `workflow-1-inbound-handler.json` — webhook `…/webhook/lazusai-inbound/:client_id`
- `workflow-2-owner-alert.json` — webhook `…/webhook/lazusai-owner-alert/:client_id`
- `workflow-3-daily-summary.json` — cron 08:00 daily
- `workflow-4-appointment-reminders.json` — cron 18:00 daily (only acts on
  booking-enabled clients; texts next-day customers a reminder)

Set these n8n **environment variables** (Settings → Variables, or the n8n
process env) so the Code nodes can reach the stack:

```
CORE_API_URL=http://127.0.0.1:8003
NIM_BASE_URL=http://127.0.0.1:8000
WHISPER_URL=http://127.0.0.1:8002
BLUEBUBBLES_URL=https://<your-bluebubbles-tunnel-host>
BLUEBUBBLES_PASSWORD=<bluebubbles password>
TELEGRAM_BOT_TOKEN=<owner alert bot token>
LAZUSAI_CORE_KEY=<shared secret>
N8N_BASE_URL=https://n8n.bookistudios.com
# Optional: business-local timezone offset in minutes (used for booking dates
# and reminders), e.g. -300 for US Eastern. Defaults to UTC.
TZ_OFFSET_MIN=0
# Optional model id overrides to match deployed NIM containers:
# NIM_MODEL_DEEPSEEK, NIM_MODEL_KIMI, NIM_MODEL_GLM, NIM_MODEL_MISTRAL
```

The Core API also sends staff/owner booking alerts, so set the same
`TELEGRAM_BOT_TOKEN`, `BLUEBUBBLES_URL`, and `BLUEBUBBLES_PASSWORD` in
`/etc/lazusai/core.env`. Per-client Square/Stripe payment credentials are
**not** environment variables — they're stored per client (dashboard →
Settings → Payments, or during onboarding) so each business uses its own
Square account.

> `n8n-nodes-base.code` runs Node.js with `this.helpers.httpRequest`; no extra
> packages or `NODE_FUNCTION_ALLOW_*` flags are required.

Activate all three workflows. They are **multi-tenant**: the same WF1 instance
serves every client via the `:client_id` path param — you do **not** create a
workflow per client.

## 5. Cloudflare Worker

```bash
npm install
npx wrangler kv namespace create CLIENTS_KV     # paste id into wrangler.toml
npx wrangler secret put N8N_API_KEY             # = LAZUSAI_CORE_KEY
npm run deploy                                   # builds dashboard + deploys
```

Confirm routes in `wrangler.toml` match your zone, then:

```bash
curl https://lazusai.com/health
```

### BlueBubbles webhook

In BlueBubbles → Settings → Webhooks, add `https://lazusai.com/webhook` for the
**New Message** event. The Worker identifies the client, flags voice notes
(`.caf`/`.m4a`), and forwards to WF1.

## 6. Hermes tool

```bash
# On the VPS, alongside Hermes:
CORE_API_URL=http://127.0.0.1:8003 LAZUSAI_CORE_KEY=<secret> \
  python server/hermes/lazusai_tool.py status
```

Register it with the running Hermes process (`lazusai_tool.register(hermes)`),
or bind the `lazusai` command to `lazusai_tool.handle(text)`. Commands:

```
lazusai new "Acme Plumbing" +15551234567
lazusai status
lazusai leads acme-plumbing
lazusai pause acme-plumbing
```

## 7. Onboard the first client

```bash
CORE_API_URL=https://core.lazusai.com LAZUSAI_CORE_KEY=<secret> \
  python scripts/onboard.py
```

This creates the config JSON, the ChromaDB collection, and (if `CF_*` creds are
set on the Core API) the Worker KV routing. Otherwise push routing manually:

```bash
LAZUSAI_DEMO_CLIENT_PASS='dashboardpw' npm run sync-clients
```

Dashboard: `https://lazusai.com/admin/<client_id>` (HTTP Basic auth — the
`auth:<client_id>` KV key set by the sync script / onboarding).

## Data flow recap

1. iMessage → BlueBubbles → `lazusai.com/webhook`
2. Worker → identifies client, flags voice → `…/lazusai-inbound/<client_id>`
3. WF1: Whisper (if voice) → Core config → RAG → NIM (fallback chain) →
   escalation check → BlueBubbles reply → log turn → (if escalated) WF2
4. WF2: detect lead/escalation → log lead → Telegram the owner
5. WF3: 08:00 daily → Core summary (NIM) → Telegram the owner
6. WF4: 18:00 daily → next-day bookings → iMessage reminders + owner heads-up

### Bookings & payments (booking-enabled clients)

- WF1 recognizes staff numbers (Core `/identify`) and gives them a
  schedule-aware persona; customers get the sales/booking persona.
- When a customer confirms a service + time, the model emits a booking
  directive; WF1 calls Core `/bookings`, which checks the slot, saves it,
  mints a **Square/Stripe deposit or full-payment link** (per the client's
  own credentials), and alerts the assigned staff (iMessage) + owner
  (Telegram) — e.g. "Josh — Jacob booked a Haircut Thu Jul 2 at 5:00 PM."
- The owner manages everything from the dashboard **Calendar / Bookings /
  Team / Services / Settings** tabs.

## Security notes

- All AI inference stays on the NIM stack; no external AI APIs are called.
- `LAZUSAI_CORE_KEY` gates the Core API; the Worker and n8n send it as
  `X-LazusAI-Key`. Rotate by updating the Worker secret, n8n var, and
  `/etc/lazusai/core.env` together.
- Bind Whisper and Core API to `127.0.0.1`; expose the Core API publicly only
  through the authenticated Cloudflare Tunnel hostname.
- Tenant isolation is enforced by `client_id` everywhere; unknown inbound
  numbers are dropped by the Worker.
