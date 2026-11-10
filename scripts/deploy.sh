#!/usr/bin/env bash
# From your workstation: copy the project (and .env) to the helper over SSH, then install.
#
# Usage: scripts/deploy.sh [agent] [tv] [tv-off]      (default: agent)
#   agent   copy + install-agent.sh on the helper
#   tv      copy + build-tv.sh + install-tv.sh on the helper (the webOS CLI lives there)
#   tv-off  copy + tv-off-app.sh --install (the "PC Off" app)
#
# Needs HELPER_SSH (an ssh host alias or user@host) and HELPER_DIR in .env.
set -euo pipefail
. "$(dirname "$0")/lib.sh"

load_env
require_vars HELPER_SSH
DIR="${HELPER_DIR:-tv-wake-launcher}"
TARGETS=("$@")
[ ${#TARGETS[@]} -gt 0 ] || TARGETS=(agent)

info "copying the project to $HELPER_SSH:$DIR"
tar -C "$ROOT" --exclude=.git --exclude=.venv --exclude=__pycache__ --exclude=.pytest_cache \
    --exclude=build --exclude=dist --exclude=.env -czf - . \
  | ssh "$HELPER_SSH" "mkdir -p '$DIR' && tar -xzf - -C '$DIR'"
# .env separately, private
tr -d '\r' < "$ENV_FILE" | ssh "$HELPER_SSH" "umask 077 && cat > '$DIR/.env'"

for t in "${TARGETS[@]}"; do
    case "$t" in
        agent) ssh "$HELPER_SSH" "cd '$DIR' && bash scripts/install-agent.sh" ;;
        tv)    ssh "$HELPER_SSH" "cd '$DIR' && bash scripts/build-tv.sh && bash scripts/install-tv.sh" ;;
        tv-off) ssh "$HELPER_SSH" "cd '$DIR' && bash scripts/tv-off-app.sh --install" ;;
        *)     die "unknown target '$t' (use agent, tv and/or tv-off)" ;;
    esac
done
