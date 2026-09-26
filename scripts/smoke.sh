#!/usr/bin/env bash
# What the README promises, run against the image that actually ships.
#
# The first command in the README is `docker compose up --build`. Nothing proved that
# worked. The test suite imports the app in-process, so a Dockerfile that installed the
# wrong extras, a compose file with a stale variable, or a migration that only ever ran
# on a developer's machine would all reach somebody else's first five minutes intact --
# and the first command in a README failing is the one failure nobody files a bug about.
#
# So this walks the quickstart: bring it up, make an account, shorten something, follow
# the redirect, watch the counter move, fetch the QR code. Every step is a claim the
# front page makes.
#
# Usage: scripts/smoke.sh [base-url]      (default http://localhost:8000)

set -euo pipefail

BASE="${1:-http://localhost:8000}"
EMAIL="smoke-$$@example.com"
PASSWORD="smoke-password-123"
TARGET="https://example.com/somewhere-long"

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

step() { printf '\n== %s\n' "$1"; }
fail() { printf '\nFAILED: %s\n' "$1" >&2; exit 1; }
json() { python3 -c "import sys,json; print(json.load(sys.stdin)$1)"; }

step "the app answers at $BASE"
for i in $(seq 1 60); do
    if curl -fsS "$BASE/healthz" >/dev/null 2>&1; then break; fi
    if [ "$i" = 60 ]; then fail "nothing answered /healthz within 60s"; fi
    sleep 1
done
curl -fsS "$BASE/healthz"; echo

step "it reports itself ready, meaning it reached Postgres and Redis"
for i in $(seq 1 30); do
    if curl -fsS "$BASE/readyz" >/dev/null 2>&1; then break; fi
    if [ "$i" = 30 ]; then
        printf 'last /readyz body: '; curl -sS "$BASE/readyz" || true
        fail "never became ready -- a dependency in the compose stack is unreachable"
    fi
    sleep 1
done
curl -fsS "$BASE/readyz"; echo

step "the dashboard is served"
curl -fsS "$BASE/app/" | grep -qi "<html" || fail "/app/ did not return a page"

step "an account can be created"
curl -fsS -X POST "$BASE/auth/register" \
    -H 'content-type: application/json' \
    -d "{\"email\":\"$EMAIL\",\"password\":\"$PASSWORD\"}" >/dev/null

step "and signed in to"
TOKEN=$(curl -fsS -X POST "$BASE/auth/token" \
    --data-urlencode "username=$EMAIL" \
    --data-urlencode "password=$PASSWORD" | json '["access_token"]')
[ -n "$TOKEN" ] || fail "no access token came back"

step "a link can be shortened"
CODE=$(curl -fsS -X POST "$BASE/api/links" \
    -H "authorization: Bearer $TOKEN" \
    -H 'content-type: application/json' \
    -d "{\"target_url\":\"$TARGET\"}" | json '["code"]')
echo "code: $CODE"

step "the redirect goes where it was told"
# One request, not two: a second would be a second click and the counter below checks
# for exactly one.
headers=$(curl -sS -o /dev/null -D - "$BASE/$CODE")
status=$(printf '%s\n' "$headers" | head -1 | awk '{print $2}')
location=$(printf '%s\n' "$headers" | tr -d '\r' | awk 'tolower($1)=="location:"{print $2}')
[ "$status" = "307" ] || fail "expected 307 from /$CODE, got $status"
[ "$location" = "$TARGET" ] || fail "redirect pointed at '$location', not $TARGET"
echo "307 -> $location"

step "the click was counted"
# Recorded in a background task after the response, so the number arrives shortly after
# the redirect rather than with it.
for i in $(seq 1 20); do
    total=$(curl -fsS "$BASE/api/links/$CODE/stats" \
        -H "authorization: Bearer $TOKEN" | json '["total_clicks"]')
    if [ "$total" = "1" ]; then break; fi
    if [ "$i" = 20 ]; then fail "total_clicks stayed at $total for 10s after one click"; fi
    sleep 0.5
done
echo "total_clicks: $total"

step "the QR code is a real PNG"
curl -fsS "$BASE/api/links/$CODE/qr?format=png" \
    -H "authorization: Bearer $TOKEN" -o "$work/qr.png"
magic=$(head -c 8 "$work/qr.png" | od -An -tx1 | tr -d ' \n')
[ "$magic" = "89504e470d0a1a0a" ] || fail "that is not a PNG (first bytes: $magic)"
echo "$(wc -c <"$work/qr.png") bytes, PNG signature intact"

printf '\nThe quickstart in the README does what it says.\n'
