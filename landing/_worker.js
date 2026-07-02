// Pages worker for the lazusai.com landing site.
//
// Serves the static landing page for everything except POST /api/demo-request,
// which records the demo-booking form as a lead on the Core API under the
// internal "lazusai-website" tenant.
//
// Required Pages project settings (production env):
//   CORE_API_URL      e.g. https://core.lazusai.com
//   LAZUSAI_CORE_KEY  secret; same key the Core API expects in X-LazusAI-Key

export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    if (url.pathname === "/api/demo-request" && request.method === "POST") {
      return handleDemoRequest(request, env);
    }

    return env.ASSETS.fetch(request);
  },
};

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

function str(v) {
  return typeof v === "string" ? v.trim().slice(0, 2000) : "";
}

function json(obj, status = 200) {
  return new Response(JSON.stringify(obj), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}
