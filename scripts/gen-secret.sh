#!/usr/bin/env bash
# Print a new random shared secret. With --write, put it into .env (only if SHARED_SECRET is empty,
# or always with --write --force). After changing it, rebuild + reinstall the TV app and restart the agent.
set -euo pipefail
. "$(dirname "$0")/lib.sh"

need_cmd python3 "Install Python 3."
SECRET="$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')"

if [ "${1:-}" != "--write" ]; then
    echo "$SECRET"
    exit 0
fi

[ -f "$ENV_FILE" ] || die "$ENV_FILE not found (copy .env.example first)"
current="$(grep -E '^SHARED_SECRET=' "$ENV_FILE" | head -1 | cut -d= -f2- || true)"
if [ -n "$current" ] && [ "${2:-}" != "--force" ]; then
    die "SHARED_SECRET is already set. Use --write --force to replace it."
fi
if grep -qE '^SHARED_SECRET=' "$ENV_FILE"; then
    sed -i.bak "s|^SHARED_SECRET=.*|SHARED_SECRET=$SECRET|" "$ENV_FILE" && rm -f "$ENV_FILE.bak"
else
    echo "SHARED_SECRET=$SECRET" >> "$ENV_FILE"
fi
chmod 600 "$ENV_FILE" 2>/dev/null || true
info "wrote a new SHARED_SECRET to $ENV_FILE"
