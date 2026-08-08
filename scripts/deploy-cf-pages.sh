#!/bin/bash
# Deploy LazusAI to Cloudflare Pages via REST API (wrangler 9106 workaround)
# Source the cloudflare env file for CF_API_TOKEN first.

set -e
source ~/.hermes/.env_cloudflare
ACCOUNT="a9c77b1cff6d48f28930ed068bbba95e"
PROJECT="lazusai"
DIST="/opt/lazusai/repo/dist"

cd /opt/lazusai/repo && node scripts/build-worker.mjs
cd "$DIST"

# Build multipart body
BOUNDARY="----FormBoundary$(head -c16 /dev/urandom | xxd -p)"
BODY_FILE="/tmp/cf_deploy_body.bin"

{
    # Manifest
    printf -- "--%s\r\n" "$BOUNDARY"
    printf "Content-Disposition: form-data; name=\"manifest\"\r\n\r\n"
    MANIFEST="{"
    first=true
    for f in *; do
        [[ -f "$f" ]] || continue
        $first || MANIFEST+=","
        first=false
        case "$f" in
            *.html) ct="text/html; charset=utf-8" ;;
            *.js)   ct="application/javascript; charset=utf-8" ;;
            *)      ct="application/octet-stream" ;;
        esac
        MANIFEST+="\"/$f\":{\"name\":\"$f\",\"type\":\"$ct\"}"
    done
    MANIFEST+="}"
    printf "%s\r\n" "$MANIFEST"

    # Files
    for f in *; do
        [[ -f "$f" ]] || continue
        printf -- "--%s\r\n" "$BOUNDARY"
        printf "Content-Disposition: form-data; name=\"%s\"; filename=\"%s\"\r\n" "$f" "$f"
        case "$f" in
            *.html) printf "Content-Type: text/html; charset=utf-8\r\n" ;;
            *.js)   printf "Content-Type: application/javascript; charset=utf-8\r\n" ;;
            *)      printf "Content-Type: application/octet-stream\r\n" ;;
        esac
        printf "\r\n"
        cat "$f"
        printf "\r\n"
    done
    printf -- "--%s--\r\n" "$BOUNDARY"
} > "$BODY_FILE"

AUTH_HEADER=$(printf 'Authorization: Bearer %s' "$CF_API_TOKEN")
curl -s -X POST \
    "https://api.cloudflare.com/client/v4/accounts/${ACCOUNT}/pages/projects/${PROJECT}/deployments" \
    -H "$AUTH_HEADER" \
    -H "Content-Type: multipart/form-data; boundary=${BOUNDARY}" \
    --data-binary "@${BODY_FILE}"

rm -f "$BODY_FILE"
