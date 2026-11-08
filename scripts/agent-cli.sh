#!/usr/bin/env bash
# Talk to the agent like the TV app does. Usage: scripts/agent-cli.sh health|wake|devmode
#   wake  = the full wake flow (start or join a job, print stages until ready/failed),
#           without the TV. Does NOT launch Moonlight (that is the TV's job).
set -euo pipefail
. "$(dirname "$0")/lib.sh"
[ -x "$ROOT/agent/.venv/bin/python" ] || die "run scripts/install-agent.sh first"
cd "$ROOT/agent"
exec .venv/bin/python -m tvwake.client --env "$ENV_FILE" "${1:-health}"
