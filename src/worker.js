/**
 * LazusAI — BlueBubbles webhook receiver & admin router (Cloudflare Worker).
 *
 * Routes:
 *   POST /webhook            Receive BlueBubbles events, identify the client,
 *                            flag voice notes, route to the client's n8n
 *                            workflow.
 *   POST /webhook/lead       Receive web form leads (name, phone, email,
 *                            service, message). Requires client_id field.
 *                            Routes to n8n lead-intake workflow.
 *   GET  /admin/:client_id   Serve the single-file admin dashboard
 *                            (HTTP Basic auth, per-client credentials).
 *   GET  /api/:client_id/*   Authenticated proxy to n8n data endpoints used by
 *                            the dashboard (feed, leads, config, reindex,
 *                            toggle).
 *   GET  /health             Liveness probe.
 *
 * Bindings (see wrangler.toml):
 *   CLIENTS_KV   KV namespace. Keys:
 *                  route:<identifier> -> client_id        (phone / chat guid /
 *                                                          apple id number)
 *                  client:<client_id> -> client config JSON (string)
 *                  auth:<client_id>   -> "user:bcrypt_or_plain" dashboard creds
 *   ADMIN_HTML   Static asset (admin/dashboard.html) via [site] or text var.
 *   N8N_BASE_URL var, e.g. https://n8n.bookistudios.com
 *   N8N_WEBHOOK_PATH var, e.g. /webhook/lazusai-inbound  (client_id appended)
 *   N8N_API_KEY  secret, sent as X-LazusAI-Key to n8n + data API.
 */

import { ADMIN_HTML } from "./admin-html.generated.js";

const AUDIO_EXTENSIONS = [".caf", ".m4a", ".amr", ".aac", ".mp3", ".wav"];
const AUDIO_MIME_PREFIX = "audio/";

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    const { pathname } = url;

    try {
      if (pathname === "/health") {
        return json({ ok: true, service: "lazusai-worker" });
      }

      if (pathname === "/webhook" && request.method === "POST") {
        return await handleWebhook(request, env, ctx);
      }

      if (pathname === "/webhook/lead" && request.method === "POST") {
        return await handleLeadWebhook(request, env, ctx);
      }

      if (pathname.startsWith("/admin/")) {
        return await handleAdmin(request, env, url);
      }

      if (pathname.startsWith("/api/")) {
        return await handleApiProxy(request, env, url);
      }

      return json({ error: "not_found" }, 404);
    } catch (err) {
      return json({ error: "internal_error", detail: String(err && err.message || err) }, 500);
    }
  },
};

/* ------------------------------------------------------------------ webhook */

async function handleWebhook(request, env, ctx) {
  let event;
  try {
    event = await request.json();
  } catch {
    return json({ error: "invalid_json" }, 400);
  }

  // BlueBubbles wraps the payload as { type, data }. Only act on new messages.
  const type = event.type || event.event || "";
  if (type && !/new[-_]?message/i.test(type)) {
    return json({ ok: true, ignored: type });
  }

  const message = event.data || event.message || event;

  // Ignore our own outbound messages to avoid echo loops.
  if (message.isFromMe === true || message.is_from_me === true) {
    return json({ ok: true, ignored: "from_me" });
  }

  const parsed = parseMessage(message);
  if (!parsed.sender && !parsed.chatGuid) {
    return json({ error: "no_sender" }, 422);
  }

  // Identify which client this inbound belongs to. Try chat guid first (most
  // specific), then the sender's phone/handle, then any apple id we can see.
  const clientId = await identifyClient(env, [
    parsed.chatGuid,
    parsed.sender,
    normalizePhone(parsed.sender),
  ]);

  if (!clientId) {
    // Unknown number — drop silently (multi-tenant isolation: never guess).
    return json({ ok: true, ignored: "unknown_client", sender: parsed.sender });
  }

  const config = await getClientConfig(env, clientId);
  if (config && config.active === false) {
    return json({ ok: true, ignored: "client_inactive", client_id: clientId });
  }

  const payload = {
    client_id: clientId,
    received_at: new Date().toISOString(),
    sender: parsed.sender,
    chat_guid: parsed.chatGuid,
    message: parsed.text,
    voice_note: parsed.voiceNote,
    attachments: parsed.attachments,
    message_guid: parsed.guid,
    raw_type: type,
  };

  // Route to the client's parameterized n8n inbound workflow.
  const n8nUrl = `${trimSlash(env.N8N_BASE_URL)}${env.N8N_WEBHOOK_PATH || "/webhook/lazusai-inbound"}/${encodeURIComponent(clientId)}`;

  const forward = fetch(n8nUrl, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-LazusAI-Key": env.N8N_API_KEY || "",
      "X-LazusAI-Client": clientId,
    },
    body: JSON.stringify(payload),
  });

  // Don't block the BlueBubbles webhook on n8n processing.
  ctx.waitUntil(forward.catch(() => {}));

  return json({ ok: true, client_id: clientId, voice_note: parsed.voiceNote });
}

/* ------------------------------------------------------------- lead webhook */

/**
 * Handle a form lead submission from a client website.
 * Accepts JSON or FormData. Required fields: client_id, name, email.
 * Routes to the per-client lead-intake n8n workflow.
 */
async function handleLeadWebhook(request, env, ctx) {
  let body;
  const contentType = request.headers.get("Content-Type") || "";

  try {
    if (contentType.includes("application/json")) {
      body = await request.json();
    } else if (contentType.includes("application/x-www-form-urlencoded") ||
               contentType.includes("multipart/form-data")) {
      const formData = await request.formData();
      body = {};
      for (const [key, value] of formData.entries()) {
        body[key] = value;
      }
    } else {
      body = await request.json();
    }
  } catch {
    return json({ error: "invalid_body" }, 400);
  }

  const clientId = body.client_id || "";
  if (!clientId) {
    return json({ error: "missing_client_id" }, 400);
  }

  const name = body.name || body.full_name || "";
  const email = body.email || "";
  if (!name && !email) {
    return json({ error: "missing_required_fields", required: "name or email" }, 400);
  }

  const config = await getClientConfig(env, clientId);
  if (!config) {
    return json({ error: "unknown_client", client_id: clientId }, 404);
  }
  if (config.active === false) {
    return json({ ok: true, ignored: "client_inactive", client_id: clientId });
  }

  const payload = {
    client_id: clientId,
    received_at: new Date().toISOString(),
    source: "web_form",
    source_url: request.headers.get("Referer") || body.source_url || "",
    name,
    phone: body.phone || "",
    email,
    service: body.service || "",
    address: body.address || body.location || "",
    message: body.message || body.details || "",
    raw: body,
  };

  const leadPath = env.N8N_LEAD_WEBHOOK_PATH || "/webhook/lazusai-lead";
  const n8nUrl = `${trimSlash(env.N8N_BASE_URL)}${leadPath}/${encodeURIComponent(clientId)}`;

  const forward = fetch(n8nUrl, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-LazusAI-Key": env.N8N_API_KEY || "",
      "X-LazusAI-Client": clientId,
    },
    body: JSON.stringify(payload),
  });

  ctx.waitUntil(forward.catch(() => {}));

  return json({ ok: true, client_id: clientId, lead: true });
}

/**
 * Normalize a BlueBubbles message object into the fields we route on.
 * Handles both camelCase (BlueBubbles default) and snake_case shapes.
 */
function parseMessage(message) {
  const handle = message.handle || message.from || {};
  const sender =
    handle.address ||
    handle.phone ||
    message.address ||
    message.sender ||
    "";

  // Chat guid identifies the conversation (e.g. iMessage;-;+15551234567).
  let chatGuid = "";
  const chats = message.chats || message.chat || [];
  if (Array.isArray(chats) && chats.length) {
    chatGuid = chats[0].guid || chats[0].chatGuid || "";
  } else if (chats && typeof chats === "object") {
    chatGuid = chats.guid || "";
  }
  chatGuid = chatGuid || message.chatGuid || message.chat_guid || "";

  const rawAttachments = message.attachments || [];
  const attachments = [];
  let voiceNote = false;

  for (const att of rawAttachments) {
    const name = att.transferName || att.transfer_name || att.name || "";
    const mime = att.mimeType || att.mime_type || att.uti || "";
    const guid = att.guid || att.attachmentGuid || "";
    const lower = name.toLowerCase();
    const isAudio =
      AUDIO_EXTENSIONS.some((ext) => lower.endsWith(ext)) ||
      (typeof mime === "string" && mime.startsWith(AUDIO_MIME_PREFIX));

    // BlueBubbles serves attachment bytes from /api/v1/attachment/<guid>/download
    const downloadUrl = guid
      ? `/api/v1/attachment/${encodeURIComponent(guid)}/download`
      : att.url || "";

    if (isAudio) voiceNote = true;

    attachments.push({
      guid,
      name,
      mime_type: mime,
      is_audio: isAudio,
      url: downloadUrl,
    });
  }

  return {
    sender,
    chatGuid,
    text: message.text || message.body || "",
    guid: message.guid || message.messageGuid || "",
    attachments,
    voiceNote,
  };
}

/* --------------------------------------------------------------- KV lookups */

async function identifyClient(env, identifiers) {
  for (const id of identifiers) {
    if (!id) continue;
    const clientId = await env.CLIENTS_KV.get(`route:${id}`);
    if (clientId) return clientId;
  }
  return null;
}

async function getClientConfig(env, clientId) {
  const raw = await env.CLIENTS_KV.get(`client:${clientId}`);
  if (!raw) return null;
  try {
    return JSON.parse(raw);
  } catch {
    return null;
  }
}

/* ----------------------------------------------------------------- admin UI */

async function handleAdmin(request, env, url) {
  const clientId = url.pathname.split("/")[2];
  if (!clientId) return json({ error: "missing_client_id" }, 400);

  const authError = await requireBasicAuth(request, env, clientId);
  if (authError) return authError;

  // Dashboard HTML is bundled as a text binding; client_id is injected so the
  // single file works for every tenant without rebuilding.
  const html = (ADMIN_HTML || DASHBOARD_FALLBACK).replace(
    /__LAZUSAI_CLIENT_ID__/g,
    clientId,
  );
  return new Response(html, {
    headers: { "Content-Type": "text/html; charset=utf-8" },
  });
}

async function handleApiProxy(request, env, url) {
  // /api/:client_id/<rest...>  -> proxied to the Core API. `rest` can be a
  // multi-segment path (e.g. bookings/<id>/cancel), so forward everything
  // after the client id, not just the first segment.
  const parts = url.pathname.split("/").filter(Boolean); // ["api", id, ...rest]
  const clientId = parts[1];
  const rest = parts.slice(2).map(encodeURIComponent).join("/");
  if (!clientId) return json({ error: "missing_client_id" }, 400);

  const authError = await requireBasicAuth(request, env, clientId);
  if (authError) return authError;

  // Dashboard data is served by the LazusAI Core API on the VPS (reached via
  // the Cloudflare Tunnel). Actions: feed, leads, config (GET/POST), reindex,
  // toggle, availability, bookings (+ nested /:id, /:id/cancel), identify.
  const base = trimSlash(env.CORE_API_URL || env.N8N_BASE_URL);
  const target = `${base}/clients/${encodeURIComponent(clientId)}/${rest}${url.search}`;
  const proxied = await fetch(target, {
    method: request.method,
    headers: {
      "Content-Type": "application/json",
      "X-LazusAI-Key": env.N8N_API_KEY || "",
      "X-LazusAI-Client": clientId,
    },
    body: request.method === "GET" || request.method === "HEAD" ? undefined : await request.text(),
  });
  return new Response(proxied.body, {
    status: proxied.status,
    headers: { "Content-Type": "application/json" },
  });
}

/**
 * HTTP Basic auth against KV key auth:<client_id> ("user:password").
 * Returns a 401 Response on failure, or null on success.
 */
async function requireBasicAuth(request, env, clientId) {
  const expected = await env.CLIENTS_KV.get(`auth:${clientId}`);
  if (!expected) {
    return json({ error: "no_credentials_configured", client_id: clientId }, 403);
  }
  const header = request.headers.get("Authorization") || "";
  if (header.startsWith("Basic ")) {
    let decoded = "";
    try {
      decoded = atob(header.slice(6));
    } catch {
      decoded = "";
    }
    if (timingSafeEqual(decoded, expected)) return null;
  }
  return new Response("Authentication required", {
    status: 401,
    headers: { "WWW-Authenticate": `Basic realm="LazusAI ${clientId}"` },
  });
}

/* ------------------------------------------------------------------ helpers */

function json(obj, status = 200) {
  return new Response(JSON.stringify(obj), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function trimSlash(s) {
  return (s || "").replace(/\/+$/, "");
}

function normalizePhone(s) {
  if (!s) return "";
  const digits = String(s).replace(/[^\d+]/g, "");
  if (!digits) return "";
  return digits.startsWith("+") ? digits : `+${digits}`;
}

function timingSafeEqual(a, b) {
  if (a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return diff === 0;
}

// Minimal fallback shown if ADMIN_HTML binding is missing. The real dashboard
// lives in admin/dashboard.html and is bound via wrangler [site]/text.
const DASHBOARD_FALLBACK = `<!doctype html><meta charset=utf-8><title>LazusAI</title><body style="background:#0a0a0f;color:#e8e8f0;font-family:system-ui;padding:3rem"><h1>LazusAI · __LAZUSAI_CLIENT_ID__</h1><p>Dashboard asset not bound. Deploy with admin/dashboard.html.</p></body>`;
