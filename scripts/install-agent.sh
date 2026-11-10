#!/usr/bin/env bash
# Install the agent on the helper (Ubuntu): venv, dependencies, systemd service + dev mode timer.
#
# Usage: scripts/install-agent.sh [--test]
#   --test   also install test dependencies and run the unit tests first
#
# SYSTEMD_MODE in .env:
#   user   (default) units run as you under `systemctl --user`; needs no sudo, but needs
#          "linger" so they run without you being logged in (we try to enable it).
#   system units go to /etc/systemd/system and run as you; needs sudo.
set -euo pipefail
. "$(dirname "$0")/lib.sh"

load_env
require_vars HELPER_HOST SHARED_SECRET
need_cmd python3 "sudo apt install python3 python3-venv"
need_cmd systemctl "This script needs systemd."
need_cmd ping "sudo apt install iputils-ping"
python3 - <<'EOF' || die "Python 3.10 or newer is required."
import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)
EOF
python3 -c 'import venv, ensurepip' 2>/dev/null || die "Python venv support missing: sudo apt install python3-venv"

MODE="${SYSTEMD_MODE:-user}"
TICK_MIN="${DEVMODE_TICK_MIN:-5}"
IPWATCH_H="${IPWATCH_EVERY_H:-5}"
VENV="$ROOT/agent/.venv"
PY="$VENV/bin/python"

chmod 600 "$ENV_FILE"

info "python venv in $VENV"
[ -x "$PY" ] || python3 -m venv "$VENV"
"$PY" -m pip install --quiet --upgrade pip
if [ "${1:-}" = "--test" ]; then
    "$PY" -m pip install --quiet -r "$ROOT/agent/requirements-dev.txt"
    info "running unit tests"
    (cd "$ROOT/agent" && "$PY" -m pytest -q)
else
    "$PY" -m pip install --quiet -r "$ROOT/agent/requirements.txt"
fi

info "checking the configuration"
(cd "$ROOT/agent" && "$PY" -c "from tvwake.config import load_from_file; c = load_from_file('$ENV_FILE'); print('  ok:', c.stream_backend, 'port', c.stream_port, '| mock' if c.mock_pc else '| real PC ' + c.pc_ip)")

render() {  # render <template> <dest> <wanted_by> <user_line>
    sed -e "s|@ROOT@|$ROOT|g" -e "s|@PYTHON@|$PY|g" -e "s|@ENV_FILE@|$ENV_FILE|g" \
        -e "s|@TICK_MIN@|$TICK_MIN|g" -e "s|@IPWATCH_H@|$IPWATCH_H|g" -e "s|@WANTED_BY@|$3|g" -e "s|@USER_LINE@|$4|g" "$1" > "$2"
}

UNITS="tvwake-agent.service tvwake-devmode.service tvwake-devmode.timer tvwake-ipwatch.service tvwake-ipwatch.timer"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

if [ "$MODE" = "user" ]; then
    DEST="$HOME/.config/systemd/user"
    mkdir -p "$DEST"
    for u in $UNITS; do render "$ROOT/systemd/$u.in" "$DEST/$u" default.target ""; done
    SCTL=(systemctl --user)
    if [ "$(loginctl show-user "$USER" -P Linger 2>/dev/null)" != "yes" ]; then
        info "enabling linger so the services run without a login session"
        loginctl enable-linger "$USER" 2>/dev/null || warn "could not enable linger. Run once:  sudo loginctl enable-linger $USER"
    fi
elif [ "$MODE" = "system" ]; then
    DEST=/etc/systemd/system
    for u in $UNITS; do render "$ROOT/systemd/$u.in" "$TMP/$u" multi-user.target "User=$USER"; done
    sudo install -m 644 "$TMP"/tvwake-* "$DEST/"
    SCTL=(sudo systemctl)
else
    die "SYSTEMD_MODE must be 'user' or 'system'"
fi

info "installing systemd units ($MODE mode) in $DEST"
"${SCTL[@]}" daemon-reload
"${SCTL[@]}" enable tvwake-agent.service tvwake-devmode.timer tvwake-ipwatch.timer >/dev/null
"${SCTL[@]}" restart tvwake-agent.service
"${SCTL[@]}" restart tvwake-devmode.timer
"${SCTL[@]}" restart tvwake-ipwatch.timer

info "waiting for the agent"
URL="http://$HELPER_HOST:${HELPER_PORT:-8787}/health"
for _ in $(seq 1 20); do
    if curl -fsS -m 2 "$URL" >/dev/null 2>&1; then
        info "agent is up: $(curl -fsS -m 2 "$URL")"
        J="journalctl$([ "$MODE" = user ] && echo ' --user')"
        echo
        echo "Logs:            $J -u tvwake-agent -f"
        echo "Dev mode timer:  $J -u tvwake-devmode"
        echo "Public IP watch: $J -u tvwake-ipwatch   (every ${IPWATCH_H} h)"
        echo "Timer schedule:  systemctl$([ "$MODE" = user ] && echo ' --user') list-timers 'tvwake-*'"
        exit 0
    fi
    sleep 0.5
done
"${SCTL[@]}" status tvwake-agent.service --no-pager -n 20 || true
die "the agent did not answer on $URL"
