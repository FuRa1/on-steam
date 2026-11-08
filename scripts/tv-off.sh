#!/usr/bin/env bash
# Switch the LG TV off over the network. The first run shows a pairing prompt on the TV:
# accept it with the remote. Usage: scripts/tv-off.sh
set -euo pipefail
. "$(dirname "$0")/lib.sh"
[ -x "$ROOT/agent/.venv/bin/python" ] || die "run scripts/install-agent.sh first"
"$ROOT/agent/.venv/bin/python" -c 'import aiowebostv' 2>/dev/null \
    || "$ROOT/agent/.venv/bin/pip" install --quiet -r "$ROOT/agent/requirements-tv.txt"
cd "$ROOT/agent"
exec .venv/bin/python -m tvwake.tvpower --env "$ENV_FILE" off
