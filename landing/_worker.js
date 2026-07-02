// Pages worker for the lazusai.com landing site.
//
// Serves the static landing page for everything except POST /api/demo-request,
// which records the demo-booking form as a lead on the Core API under the
// internal "lazusai-website" tenant.
//
// Required Pages project settings (production env):
//   CORE_API_URL          e.g. https://core.lazusai.com
//   LAZUSAI_CORE_KEY      secret; same key the Core API expects in X-LazusAI-Key
//
// Optional — Stripe self-serve signup (wizard falls back to lead capture
// until these are set):
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

  const resp = await fetch(
    `${env.CORE_API_URL}/clients/lazusai-website/leads`,
    {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-LazusAI-Key": env.LAZUSAI_CORE_KEY || "",
      },
      body: JSON.stringify(lead),
    },
  );

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

  const priceId = env[PLANS[plan]];
  if (!env.STRIPE_SECRET_KEY || !priceId) {
    // Payments not configured yet — capture the signup as a lead so nothing
    // is lost, and tell the client we'll be in touch.
    await postLead(env, {
      sender: "lazusai.com get-started wizard",
      name,
      email,
      phone: meta.phone,
      summary: `SIGNUP (payments not yet configured) — ${business} · plan: ${plan}` +
        (meta.industry ? ` · ${meta.industry}` : ""),
      message: [meta.services && `Services: ${meta.services}`,
                meta.hours && `Hours: ${meta.hours}`,
                meta.faqs && `FAQs: ${meta.faqs}`].filter(Boolean).join("\n"),
    });
    return json({ ok: true, mode: "lead" });
  }

  const params = new URLSearchParams({
    mode: "subscription",
    "line_items[0][price]": priceId,
    "line_items[0][quantity]": "1",
    customer_email: email,
    success_url: "https://lazusai.com/get-started?status=success",
    cancel_url: "https://lazusai.com/get-started?status=cancelled",
    allow_promotion_codes: "true",
  });
  for (const [k, v] of Object.entries(meta)) {
    if (v) params.set(`metadata[${k}]`, v);
  }

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
  const business = meta.business || "Unknown Business";

  // Create the tenant, inactive until an iMessage number is provisioned.
  const created = await coreFetch(env, "POST", "/clients", {
    business_name: business,
    apple_id_number: "",
    industry: meta.industry || "",
    services: meta.services ? meta.services.split(",").map((s) => s.trim()).filter(Boolean) : [],
    integrations: {
      billing: {
        processor: "stripe",
        stripe_customer_id: session.customer || "",
        stripe_subscription_id: session.subscription || "",
        plan: meta.plan || "",
        signup_email: meta.email || session.customer_details?.email || "",
        signup_phone: meta.phone || "",
        signup_notes: [meta.hours && `Hours: ${meta.hours}`,
                       meta.faqs && `FAQs: ${meta.faqs}`].filter(Boolean).join("\n"),
      },
    },
  });

  let clientId = "";
  if (created.ok) {
    const data = await created.json();
    clientId = data.client_id || "";
    if (clientId) {
      await coreFetch(env, "POST", `/clients/${clientId}/toggle?active=false`);
    }
  }

  // Surface the paid signup where the operator already looks for leads.
  await postLead(env, {
    sender: "stripe checkout",
    name: meta.name || "",
    email: meta.email || session.customer_details?.email || "",
    phone: meta.phone || "",
    summary: `💰 PAID SIGNUP — ${business} · plan: ${meta.plan || "?"} · ` +
      `client_id: ${clientId || "CREATE FAILED"} · needs iMessage number provisioning`,
    message: `Stripe customer: ${session.customer || "?"} · subscription: ${session.subscription || "?"}`,
    escalated: true,
  });

  return json({ ok: true, client_id: clientId });
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
  return fetch(`${env.CORE_API_URL}${path}`, {
    method,
    headers: {
      "Content-Type": "application/json",
      "X-LazusAI-Key": env.LAZUSAI_CORE_KEY || "",
    },
    body: body ? JSON.stringify(body) : undefined,
  });
}

async function postLead(env, lead) {
  try {
    await coreFetch(env, "POST", "/clients/lazusai-website/leads", lead);
  } catch {
    // best-effort
  }
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
