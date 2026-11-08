#!/usr/bin/env bash
# Build the TV app: generate config.js/appinfo.json from .env and package an IPK into dist/.
set -euo pipefail
. "$(dirname "$0")/lib.sh"

load_env
require_vars HELPER_HOST SHARED_SECRET APP_ID APP_TITLE
need_cmd python3 "Install Python 3 (sudo apt install python3)."
need_ares ares-package

APP="$ROOT/tv-app"
STAGE="$ROOT/build/tv-app"
DIST="$ROOT/dist"

python3 "$ROOT/scripts/render_tv.py" "$APP"

# LG's webOSTV.js: optional (luna.js falls back to PalmServiceBridge), copied from the
# webOS CLI's own "webostvjs" template if it is not in the repo yet.
if [ ! -f "$APP/js/lib/webOSTV.js" ]; then
    CLI_DIR="$(dirname "$(readlink -f "$(command -v ares-package)")")/.."
    SRC="$(find "$CLI_DIR" -name 'webOSTV.js' -path '*webOSTVjs*' 2>/dev/null | head -1 || true)"
    if [ -n "$SRC" ]; then
        cp "$SRC" "$APP/js/lib/webOSTV.js"
        info "copied webOSTV.js from the webOS CLI ($SRC)"
    else
        warn "webOSTV.js not found; the app will use PalmServiceBridge directly (see README)"
    fi
fi

for f in icon.png largeIcon.png; do
    [ -f "$APP/$f" ] || python3 "$ROOT/scripts/make-icons.py" "$APP"
done

info "staging app in $STAGE"
rm -rf "$STAGE"
mkdir -p "$STAGE" "$DIST"
cp -r "$APP/index.html" "$APP/appinfo.json" "$APP/config.js" "$APP/css" "$APP/js" \
      "$APP/icon.png" "$APP/largeIcon.png" "$STAGE/"

info "packaging"
ares-package --no-minify -o "$DIST" "$STAGE"
IPK="$DIST/${APP_ID}_${APP_VERSION:-1.0.0}_all.ipk"
[ -f "$IPK" ] || die "expected $IPK was not created"
info "built $IPK"
