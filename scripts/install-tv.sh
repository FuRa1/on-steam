#!/usr/bin/env bash
# Install the built IPK on the TV. Usage: scripts/install-tv.sh [--launch]
set -euo pipefail
. "$(dirname "$0")/lib.sh"

load_env
require_vars TV_DEVICE_NAME APP_ID
need_ares ares-install ares-launch

IPK="$ROOT/dist/${APP_ID}_${APP_VERSION:-1.0.0}_all.ipk"
[ -f "$IPK" ] || die "$IPK not found. Run scripts/build-tv.sh first."

ares-setup-device -l 2>/dev/null | grep -q "^$TV_DEVICE_NAME\b" \
    || die "ares device '$TV_DEVICE_NAME' is not set up. Run scripts/setup-tv-key.sh (see README)."

info "installing $IPK on $TV_DEVICE_NAME"
ares-install -d "$TV_DEVICE_NAME" "$IPK"

if [ "${1:-}" = "--launch" ]; then
    ares-launch -d "$TV_DEVICE_NAME" "$APP_ID"
fi
info "done. The app '$APP_TITLE' is in the TV's app list."
