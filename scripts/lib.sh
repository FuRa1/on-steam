# Shared helpers for the scripts. Source it: . "$(dirname "$0")/lib.sh"
# shellcheck shell=bash

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${ENV_FILE:-$ROOT/.env}"

die()  { echo "ERROR: $*" >&2; exit 1; }
info() { echo "==> $*"; }
warn() { echo "WARNING: $*" >&2; }

load_env() {
    [ -f "$ENV_FILE" ] || die "$ENV_FILE not found. Copy .env.example to .env and fill it in."
    # Strip Windows line endings (CR) so an .env edited on Windows does not add "\r" to values.
    set -a
    # shellcheck disable=SC1090
    . <(tr -d '\r' < "$ENV_FILE")
    set +a
}

require_vars() {
    local v
    for v in "$@"; do
        [ -n "${!v:-}" ] || die "$v is not set in $ENV_FILE"
    done
}

need_cmd() {  # need_cmd <command> <how to install>
    command -v "$1" >/dev/null 2>&1 || die "'$1' not found. $2"
}

need_ares() {
    # The CLI is installed per user (npm --prefix ~/.local), so make sure it is on PATH.
    export PATH="$HOME/.local/bin:$PATH"
    local c
    for c in "$@"; do
        need_cmd "$c" "Install the webOS CLI: npm install -g --prefix ~/.local @webos-tools/cli  (needs Node.js; see README)"
    done
}

expand_home() { local p="$1"; echo "${p/#\~/$HOME}"; }
