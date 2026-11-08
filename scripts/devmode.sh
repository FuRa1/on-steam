#!/usr/bin/env bash
# Developer Mode renewal by hand. Usage: scripts/devmode.sh status|tick|renew
#   status  show the stored state (remaining time, last result)
#   tick    do whatever is due (what the timer runs)
#   renew   extend + cloud reset + check right now
set -euo pipefail
. "$(dirname "$0")/lib.sh"
[ -x "$ROOT/agent/.venv/bin/python" ] || die "run scripts/install-agent.sh first"
cd "$ROOT/agent"
exec .venv/bin/python -m tvwake.devmode --env "$ENV_FILE" "${1:-status}"
