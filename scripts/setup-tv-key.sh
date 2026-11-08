#!/usr/bin/env bash
# One-time: register the TV with the webOS CLI, download its SSH key, and make the
# passphrase-free copy the agent uses for dev mode renewal (TV_SSH_KEY).
#
# Before running: on the TV open the "Developer Mode" app and switch "Key Server" ON.
# You will be asked for the Passphrase shown in that app. Switch Key Server OFF afterwards.
set -euo pipefail
. "$(dirname "$0")/lib.sh"

load_env
require_vars TV_IP TV_DEVICE_NAME
need_ares ares-setup-device ares-novacom
need_cmd ssh-keygen "sudo apt install openssh-client"

PORT="${TV_SSH_PORT:-9922}"
KEY_OUT="$(expand_home "${TV_SSH_KEY:-~/.ssh/tvwake_tv}")"
ARES_KEY="$HOME/.ssh/${TV_DEVICE_NAME}_webos"

read -r -p "Passphrase shown in the TV's Developer Mode app: " PASS
[ -n "$PASS" ] || die "no passphrase given"

if ares-setup-device -l | grep -q "^$TV_DEVICE_NAME\b"; then
    ACTION=-m
else
    ACTION=-a
fi
ares-setup-device "$ACTION" "$TV_DEVICE_NAME" -i "host=$TV_IP" -i "port=$PORT" -i "username=prisoner" \
    -i "privatekey=${TV_DEVICE_NAME}_webos" -i "passphrase=$PASS" -i "default=true" >/dev/null

info "downloading the key from the TV (Key Server must be ON)"
ares-novacom --device "$TV_DEVICE_NAME" --getkey <<<"$PASS" >/dev/null
[ -f "$ARES_KEY" ] || die "key download failed; is Key Server ON?"
# --getkey may reset the device entry; set key and passphrase again.
ares-setup-device -m "$TV_DEVICE_NAME" -i "privatekey=${TV_DEVICE_NAME}_webos" -i "passphrase=$PASS" >/dev/null

info "creating the agent's key copy at $KEY_OUT"
install -m 600 "$ARES_KEY" "$KEY_OUT"
ssh-keygen -p -P "$PASS" -N "" -f "$KEY_OUT" >/dev/null

info "testing ssh"
ssh -i "$KEY_OUT" -p "$PORT" -o BatchMode=yes -o ConnectTimeout=5 -o StrictHostKeyChecking=accept-new \
    -o PubkeyAcceptedAlgorithms=+ssh-rsa -o HostKeyAlgorithms=+ssh-rsa \
    "prisoner@$TV_IP" "test -r /var/luna/preferences/devmode_enabled" \
    || die "ssh works but the token file is not readable"
info "ok. You can switch Key Server OFF on the TV now."
info "Run a renewal now with:  scripts/devmode.sh renew"
