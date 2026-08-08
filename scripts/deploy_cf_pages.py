"""Deploy LazusAI dist/ to Cloudflare Pages via REST API (no wrangler).

Builds the multipart form body exactly like the old bash script, then POSTs.
Reads the CF token at runtime from ~/.hermes/.env_cloudflare or ~/.hermes/.env.
"""
import json
import os
import pathlib
import uuid
import urllib.error
import urllib.request

ACCOUNT = "a9c77b1cff6d48f28930ed068bbba95e"
PROJECT = "lazusai"
DIST = "/opt/lazusai/repo/dist"


def load_env(path):
    out = {}
    try:
        for line in pathlib.Path(path).read_text().splitlines():
            line = line.strip()
            if "=" in line and not line.startswith("#"):
                k, _, v = line.partition("=")
                out[k.strip()] = v.strip().strip('"')
    except FileNotFoundError:
        pass
    return out


def find_token():
    for p in ("/root/.hermes/.env_cloudflare", "/root/.hermes/.env"):
        env = load_env(p)
        for k in ("CF_API_TOKEN", "CLOUDFLARE_API_TOKEN", "CLOUDFLARE_API_KEY"):
            if env.get(k):
                return env[k]
    return ""


def content_type(name):
    if name.endswith(".html"):
        return "text/html; charset=utf-8"
    if name.endswith(".js"):
        return "application/javascript; charset=utf-8"
    return "application/octet-stream"


def main():
    token = find_token()
    if not token:
        print("ERROR: no CF token found")
        return 1

    files = sorted(p for p in pathlib.Path(DIST).iterdir() if p.is_file())
    boundary = "----FormBoundary" + uuid.uuid4().hex
    parts = []

    manifest = {"/" + f.name: {"name": f.name, "type": content_type(f.name)} for f in files}
    parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"manifest\"\r\n\r\n")
    parts.append(json.dumps(manifest))
    parts.append("\r\n")

    for f in files:
        parts.append(f"--{boundary}\r\n")
        parts.append(f'Content-Disposition: form-data; name="{f.name}"; filename="{f.name}"\r\n')
        parts.append(f"Content-Type: {content_type(f.name)}\r\n\r\n")
        parts.append(f.read_bytes())
        parts.append("\r\n")
    parts.append(f"--{boundary}--\r\n")

    body = b"".join(p if isinstance(p, bytes) else p.encode() for p in parts)
    url = (f"https://api.cloudflare.com/client/v4/accounts/{ACCOUNT}"
           f"/pages/projects/{PROJECT}/deployments")
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Authorization": "Bearer " + token,
            "Content-Type": "multipart/form-data; boundary=" + boundary,
        },
    )
    try:
        resp = urllib.request.urlopen(req, timeout=120)
        data = json.loads(resp.read())
        r = data.get("result") or {}
        print("deploy success:", data.get("success"), "| id:", str(r.get("id", ""))[:12],
              "| url:", r.get("url", ""))
        return 0
    except urllib.error.HTTPError as e:
        print("DEPLOY FAILED HTTP", e.code, ":", e.read()[:400])
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
