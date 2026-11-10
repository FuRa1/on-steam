#!/usr/bin/env bash
# Build (and with --install, install) the "PC Off" TV app: a second icon next to "PC Game" that
# shuts the PC down after a confirmation (agent: POST /shutdown, needs PC_SSH).
# Runs on the helper (the webOS CLI lives there); from the workstation: scripts/deploy.sh tv-off
#
# It shares css/ and js/ (api, hmac, luna) with tv-app/ but never changes tv-app/ or its build.
set -euo pipefail
. "$(dirname "$0")/lib.sh"

load_env
require_vars HELPER_HOST SHARED_SECRET
need_cmd python3 "Install Python 3 (sudo apt install python3)."
need_ares ares-package
# Its own id and title (the .env APP_ID/APP_TITLE belong to "PC Game").
export APP_ID="${OFF_APP_ID:-com.home.tvwake.off}"
export APP_TITLE="${OFF_APP_TITLE:-PC Off}"
export APP_VERSION="${OFF_APP_VERSION:-1.0.0}"

APP="$ROOT/tv-off-app"
STAGE="$ROOT/build/tv-off-app"
DIST="$ROOT/dist"

python3 "$ROOT/scripts/render_tv.py" "$APP"
for f in icon.png largeIcon.png; do
    [ -f "$APP/$f" ] || python3 "$ROOT/scripts/make-icons.py" --power "$APP"
done

info "staging app in $STAGE"
rm -rf "$STAGE"
mkdir -p "$STAGE/js/lib" "$STAGE/css" "$DIST"
cp "$APP/index.html" "$APP/appinfo.json" "$APP/config.js" "$APP/icon.png" "$APP/largeIcon.png" "$STAGE/"
cp "$APP/js/off.js" "$ROOT/tv-app/js/api.js" "$ROOT/tv-app/js/hmac.js" "$ROOT/tv-app/js/luna.js" "$STAGE/js/"
cp "$ROOT/tv-app/js/lib/"*.js "$STAGE/js/lib/"
cp "$ROOT/tv-app/css/app.css" "$STAGE/css/"

info "packaging"
ares-package --no-minify -o "$DIST" "$STAGE"
IPK="$DIST/${APP_ID}_${APP_VERSION}_all.ipk"
[ -f "$IPK" ] || die "expected $IPK was not created"
info "built $IPK"

if [ "${1:-}" = "--install" ]; then
    require_vars TV_DEVICE_NAME
    need_ares ares-install
    ares-setup-device -l 2>/dev/null | grep -q "^$TV_DEVICE_NAME\b" \
        || die "ares device '$TV_DEVICE_NAME' is not set up. Run scripts/setup-tv-key.sh (see README)."
    info "installing $IPK on $TV_DEVICE_NAME"
    ares-install -d "$TV_DEVICE_NAME" "$IPK"
    info "done. '$APP_TITLE' is in the TV's app list."
fi
