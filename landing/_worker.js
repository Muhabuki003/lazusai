// Pages worker for the lazusai.com landing site.
//
// Serves the static landing page for everything except POST /api/demo-request,
// which records the demo-booking form as a lead on the Core API under the
// internal "lazusai-website" tenant.
//
// Required Pages project settings (production env):
//   CORE_API_URL          e.g. https://core.lazusai.com
//   LAZUSAI_CORE_KEY      secret; same key the Core API expects in X-LazusAI-Key
//   ADMIN_USER            HTTP Basic auth username for /admin*
//   ADMIN_PASSWORD        HTTP Basic auth password (secret) for /admin*
//
// Optional — Stripe self-serve signup (wizard falls back to a queued signup
// with no payment until these are set):
//   STRIPE_SECRET_KEY     sk_test_... / sk_live_...
//   STRIPE_WEBHOOK_SECRET whsec_... (from the Stripe webhook endpoint config)
//   STRIPE_PRICE_STARTER  price_... (monthly subscription price id)
//   STRIPE_PRICE_PRO      price_...

export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    if (url.pathname === "/api/demo-request" && request.method === "POST") {
      return handleDemoRequest(request, env);
    }

    // BlueBubbles posts new-message events here; forward to the Core API
    // with the shared key (BlueBubbles itself can't send custom headers).
    if (url.pathname === "/webhook" && request.method === "POST") {
      return forwardWebhook(request, env);
    }

    if (url.pathname === "/api/signup" && request.method === "POST") {
      return handleSignup(request, env);
    }

    if (url.pathname === "/api/stripe-webhook" && request.method === "POST") {
      return handleStripeWebhook(request, env);
    }

    // Everything below is operator-only: gate on HTTP Basic auth.
    const isOperatorRoute =
      url.pathname === "/admin" ||
      url.pathname.startsWith("/admin/") ||
      url.pathname === "/api/signups" ||
      url.pathname.startsWith("/api/signups/") ||
      url.pathname === "/api/_clients" ||
      (url.pathname.startsWith("/api/") &&
        url.pathname !== "/api/demo-request" &&
        url.pathname !== "/api/signup" &&
        url.pathname !== "/api/stripe-webhook");

    if (isOperatorRoute) {
      const authError = requireBasicAuth(request, env);
      if (authError) return authError;

      if (url.pathname === "/admin") {
        return env.ASSETS.fetch(new URL("/admin-operator.html", request.url));
      }
      if (url.pathname.startsWith("/admin/")) {
        return handleClientDashboard(request, env, url);
      }
      if (url.pathname === "/api/_clients") {
        return proxyToCore(env, "GET", "/clients");
      }
      if (url.pathname === "/api/signups" || url.pathname.startsWith("/api/signups/")) {
        return proxyToCore(env, request.method, url.pathname.replace("/api", "") + url.search,
          request.method === "GET" ? undefined : await request.text());
      }
      // /api/:client_id/* — per-client dashboard data proxy.
      const parts = url.pathname.split("/").filter(Boolean); // ["api", id, ...rest]
      const rest = parts.slice(2).map(encodeURIComponent).join("/");
      return proxyToCore(env, request.method, `/clients/${encodeURIComponent(parts[1])}/${rest}${url.search}`,
        request.method === "GET" || request.method === "HEAD" ? undefined : await request.text());
    }

    return env.ASSETS.fetch(request);
  },
};

async function forwardWebhook(request, env) {
  let body;
  try {
    body = await request.text();
  } catch {
    return json({ error: "bad_body" }, 400);
  }
  const resp = await fetch(`${env.CORE_API_URL}/webhook`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-LazusAI-Key": env.LAZUSAI_CORE_KEY || "",
    },
    body,
  });
  return json({ ok: resp.ok }, resp.ok ? 200 : 502);
}

async function handleDemoRequest(request, env) {
  let body;
  try {
    body = await request.json();
  } catch {
    return json({ error: "invalid_json" }, 400);
  }

  const name = str(body.name);
  const email = str(body.email);
  const business = str(body.business);
  if (!name || !email || !business) {
    return json({ error: "missing_fields" }, 400);
  }

  const summaryParts = [
    `Demo request from ${business}`,
    str(body.industry) && `industry: ${str(body.industry)}`,
    str(body.missed_leads) && `missed leads/mo: ${str(body.missed_leads)}`,
  ].filter(Boolean);

  const lead = {
    sender: "lazusai.com contact form",
    name,
    phone: str(body.phone),
    email,
    summary: summaryParts.join(" · "),
    message: str(body.message),
    escalated: false,
  };

  const resp = await coreFetch(env, "POST", "/clients/lazusai-website/leads", lead);
  if (!resp.ok) {
    return json({ error: "upstream_failed" }, 502);
  }
  return json({ ok: true });
}

/* ------------------------------------------------------- self-serve signup */

const PLANS = { starter: "STRIPE_PRICE_STARTER", pro: "STRIPE_PRICE_PRO" };

async function handleSignup(request, env) {
  let body;
  try {
    body = await request.json();
  } catch {
    return json({ error: "invalid_json" }, 400);
  }

  const name = str(body.name);
  const email = str(body.email);
  const business = str(body.business);
  const plan = str(body.plan).toLowerCase();
  if (!name || !email || !business || !PLANS[plan]) {
    return json({ error: "missing_fields" }, 400);
  }

  const meta = {
    name,
    email,
    business,
    plan,
    phone: str(body.phone),
    industry: str(body.industry),
    services: str(body.services).slice(0, 480),
    hours: str(body.hours).slice(0, 480),
    faqs: str(body.faqs).slice(0, 480),
  };

  // Every submission becomes a pending signup — reviewed and approved by the
  // operator at /admin regardless of whether payment is configured. This is
  // the single source of truth the wizard writes to; nothing is provisioned
  // automatically just because a form was filled out.
  const signupResp = await coreFetch(env, "POST", "/signups", {
    name, email, phone: meta.phone, business, industry: meta.industry,
    services: meta.services, hours: meta.hours, faqs: meta.faqs, plan,
  });
  if (!signupResp.ok) {
    return json({ error: "signup_failed" }, 502);
  }
  const { id: signupId } = await signupResp.json();

  const priceId = env[PLANS[plan]];
  if (!env.STRIPE_SECRET_KEY || !priceId) {
    return json({ ok: true, mode: "queued", signup_id: signupId });
  }

  const params = new URLSearchParams({
    mode: "subscription",
    "line_items[0][price]": priceId,
    "line_items[0][quantity]": "1",
    customer_email: email,
    success_url: "https://lazusai.com/get-started?status=success",
    cancel_url: "https://lazusai.com/get-started?status=cancelled",
    allow_promotion_codes: "true",
    "metadata[signup_id]": signupId,
  });

  const resp = await fetch("https://api.stripe.com/v1/checkout/sessions", {
    method: "POST",
    headers: {
      Authorization: `Bearer ${env.STRIPE_SECRET_KEY}`,
      "Content-Type": "application/x-www-form-urlencoded",
    },
    body: params,
  });
  const session = await resp.json();
  if (!resp.ok || !session.url) {
    return json({ error: "stripe_error" }, 502);
  }
  return json({ ok: true, mode: "checkout", checkout_url: session.url });
}

async function handleStripeWebhook(request, env) {
  const payload = await request.text();
  const sig = request.headers.get("Stripe-Signature") || "";
  if (!env.STRIPE_WEBHOOK_SECRET ||
      !(await verifyStripeSignature(payload, sig, env.STRIPE_WEBHOOK_SECRET))) {
    return json({ error: "bad_signature" }, 400);
  }

  let event;
  try {
    event = JSON.parse(payload);
  } catch {
    return json({ error: "invalid_json" }, 400);
  }

  if (event.type !== "checkout.session.completed") {
    return json({ ok: true, ignored: event.type });
  }

  const session = event.data && event.data.object ? event.data.object : {};
  const meta = session.metadata || {};
  const signupId = meta.signup_id || "";
  if (!signupId) {
    return json({ ok: true, ignored: "no_signup_id" });
  }

  // Payment does NOT provision the tenant — it flags the queued signup as
  // paid (and Telegram-notifies the operator) so it can still be reviewed
  // and approved at /admin like any other signup.
  await coreFetch(env, "POST", `/signups/${signupId}/mark-paid`, {
    stripe_customer_id: session.customer || "",
    stripe_subscription_id: session.subscription || "",
  });

  return json({ ok: true, signup_id: signupId });
}

async function verifyStripeSignature(payload, sigHeader, secret) {
  const parts = Object.fromEntries(
    sigHeader.split(",").map((p) => p.split("=").map((s) => s.trim())),
  );
  const timestamp = parts.t;
  const expected = parts.v1;
  if (!timestamp || !expected) return false;
  // Reject events older than 5 minutes (replay protection).
  if (Math.abs(Date.now() / 1000 - Number(timestamp)) > 300) return false;

  const key = await crypto.subtle.importKey(
    "raw", new TextEncoder().encode(secret),
    { name: "HMAC", hash: "SHA-256" }, false, ["sign"],
  );
  const mac = await crypto.subtle.sign(
    "HMAC", key, new TextEncoder().encode(`${timestamp}.${payload}`),
  );
  const computed = [...new Uint8Array(mac)]
    .map((b) => b.toString(16).padStart(2, "0")).join("");
  if (computed.length !== expected.length) return false;
  let diff = 0;
  for (let i = 0; i < computed.length; i++) {
    diff |= computed.charCodeAt(i) ^ expected.charCodeAt(i);
  }
  return diff === 0;
}

async function coreFetch(env, method, path, body) {
  // `body` may be a plain object (JSON-encoded here) or an already-serialized
  // string (passed through as-is — used when proxying a request we already
  // read the text of, so we don't double-encode it).
  return fetch(`${env.CORE_API_URL}${path}`, {
    method,
    headers: {
      "Content-Type": "application/json",
      "X-LazusAI-Key": env.LAZUSAI_CORE_KEY || "",
    },
    body: body == null ? undefined : (typeof body === "string" ? body : JSON.stringify(body)),
  });
}

/* -------------------------------------------------------- operator admin */

function requireBasicAuth(request, env) {
  const user = env.ADMIN_USER || "";
  const pass = env.ADMIN_PASSWORD || "";
  if (!user || !pass) {
    return json({ error: "admin_not_configured" }, 503);
  }
  const header = request.headers.get("Authorization") || "";
  if (header.startsWith("Basic ")) {
    let decoded = "";
    try {
      decoded = atob(header.slice(6));
    } catch {
      decoded = "";
    }
    if (timingSafeEqual(decoded, `${user}:${pass}`)) return null;
  }
  return new Response("Authentication required", {
    status: 401,
    headers: { "WWW-Authenticate": 'Basic realm="LazusAI Admin"' },
  });
}

function timingSafeEqual(a, b) {
  if (a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return diff === 0;
}

async function handleClientDashboard(request, env, url) {
  const clientId = url.pathname.split("/")[2];
  if (!clientId) return json({ error: "missing_client_id" }, 400);
  const asset = await env.ASSETS.fetch(new URL("/admin-dashboard.html", request.url));
  const html = (await asset.text()).replace(/__LAZUSAI_CLIENT_ID__/g, clientId);
  return new Response(html, { headers: { "Content-Type": "text/html; charset=utf-8" } });
}

async function proxyToCore(env, method, path, body) {
  const resp = await coreFetch(env, method, path, body);
  return new Response(resp.body, {
    status: resp.status,
    headers: { "Content-Type": "application/json" },
  });
}

function str(v) {
  return typeof v === "string" ? v.trim().slice(0, 2000) : "";
}

function json(obj, status = 200) {
  return new Response(JSON.stringify(obj), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}
