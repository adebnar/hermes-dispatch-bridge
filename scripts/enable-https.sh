#!/bin/bash
# Front the bridge with HTTPS on your tailnet using a valid Let's Encrypt cert
# (provisioned automatically by Tailscale). Requires "HTTPS Certificates" enabled
# for your tailnet in the Tailscale admin console (Settings → Keys/Features).
set -e
TS="${TAILSCALE_BIN:-/Applications/Tailscale.app/Contents/MacOS/Tailscale}"
[ -x "$TS" ] || TS="$(command -v tailscale)"
"$TS" serve --bg 8099
echo
echo "Your HTTPS bridge URL (use this in the app):"
echo "  https://$("$TS" status --json | python3 -c 'import sys,json;print(json.load(sys.stdin)["Self"]["DNSName"].rstrip("."))')"
