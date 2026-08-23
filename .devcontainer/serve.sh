#!/usr/bin/env bash
# Start the app with BASE_URL pointing at wherever this instance is actually reachable.
#
# Short links are built from BASE_URL. In a Codespace the app answers on a forwarded
# hostname, not on localhost, so leaving the default in place would hand out links that
# look right in the dashboard and lead nowhere the moment anyone copies one.
set -euo pipefail

if [ -n "${CODESPACE_NAME:-}" ]; then
  domain="${GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN:-app.github.dev}"
  export BASE_URL="https://${CODESPACE_NAME}-8000.${domain}"
  cat <<EOF

==> ${BASE_URL}

    If that address shows "This page isn't working", the forwarded port is private and
    the sign-in postback did not complete. In the PORTS tab, right-click port 8000 ->
    Port Visibility -> Public, then reload. Nothing here needs protecting: this Codespace
    is yours alone and goes away when you delete it.

EOF
fi

exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
