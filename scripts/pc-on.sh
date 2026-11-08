#!/usr/bin/env bash
# Wake the gaming PC by hand (Wake-on-LAN to PC_MAC) and wait until it answers
# (ping or the streaming port). For the full flow with stages use: scripts/agent-cli.sh wake
set -euo pipefail
. "$(dirname "$0")/lib.sh"

load_env
require_vars PC_MAC PC_IP WOL_BROADCAST
[ -x "$ROOT/agent/.venv/bin/python" ] || die "run scripts/install-agent.sh first"
PORT="${STREAM_PORT:-47989}"

cd "$ROOT/agent"
.venv/bin/python - "$PC_MAC" "$WOL_BROADCAST" <<'EOF'
import sys, time
from tvwake.config import normalize_mac
from tvwake.wol import send_magic_packet
mac = normalize_mac(sys.argv[1])
for _ in range(3):
    send_magic_packet(mac, sys.argv[2], (9, 7))
    time.sleep(0.5)
print(f"magic packet sent to {mac}")
EOF

for i in $(seq 1 180); do
    if ping -c1 -W1 "$PC_IP" >/dev/null 2>&1 || timeout 1 bash -c "</dev/tcp/$PC_IP/$PORT" 2>/dev/null; then
        info "PC answers after ${i}s"
        exit 0
    fi
    sleep 1
done
die "PC did not answer within 180 s (see README troubleshooting, stage 4)"
