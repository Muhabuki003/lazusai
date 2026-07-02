# BlueBubbles on your Mac — the one manual hour

Everything server-side (webhook receiver, AI pipeline, reply delivery, logging,
lead alerts) is already deployed and waiting. This doc is the only part that
needs a human with a Mac. Total time: ~45–60 minutes.

## What you need

- A Mac that stays on and connected (a Mac mini in a closet is perfect).
- An Apple ID dedicated to the business number (do NOT use your personal one —
  every client message flows through it).
- The Mac signed into **Messages.app** with that Apple ID, send/receive working
  (send yourself a test iMessage first).

## Step 1 — Install BlueBubbles server (~15 min)

1. Download the latest server from https://bluebubbles.app/downloads (macOS server app).
2. Open it and follow the setup wizard:
   - Grant **Full Disk Access** and **Accessibility** when prompted
     (System Settings → Privacy & Security).
   - Choose the **Private API** setup when offered (needed for typing
     indicators and reliable sends) — the wizard walks you through enabling it.
3. In BlueBubbles → **Settings → Server**, note the **server password** — you
   will send me this.

## Step 2 — Expose it with a Cloudflare Tunnel (~15 min)

In Terminal on the Mac:

```bash
brew install cloudflared
cloudflared tunnel login            # opens browser — pick the lazusai.com zone
cloudflared tunnel create bluebubbles-mac
cloudflared tunnel route dns bluebubbles-mac bb.lazusai.com
```

Create `~/.cloudflared/config.yml`:

```yaml
tunnel: bluebubbles-mac
credentials-file: /Users/YOURUSER/.cloudflared/<TUNNEL_UUID>.json
ingress:
  - hostname: bb.lazusai.com
    service: http://localhost:1234    # BlueBubbles default port
  - service: http_status:404
```

Then install it as a service so it survives reboots:

```bash
sudo cloudflared service install
```

Verify from any browser: `https://bb.lazusai.com` should show the BlueBubbles
landing/auth page.

## Step 3 — Point the webhook at the platform (~2 min)

In BlueBubbles → **Settings → Webhooks** → Add:

- URL: `https://lazusai.com/webhook`
- Events: **New Message** (only)

## Step 4 — Hand back two values

Send me:

1. The BlueBubbles **server password**
2. The tunnel hostname (`bb.lazusai.com` if you followed the above)

They get set as `BLUEBUBBLES_URL` / `BLUEBUBBLES_PASSWORD` in
`/etc/lazusai/core.env` (and the Whisper service env), the core service
restarts, and we run the first live end-to-end test: text the business number
→ AI answers over iMessage.

## Gotchas

- **Keep the Mac awake**: System Settings → Energy → prevent sleep when
  display is off (or `sudo pmset -a sleep 0`).
- **macOS updates** log you out of Messages sometimes — if replies stop,
  check Messages.app is still signed in.
- Each additional client business later needs its own Apple ID signed into a
  Mac (same Mac with multiple users, or more Minis) — plan for this before
  selling plan #2.
