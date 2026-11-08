#!/usr/bin/env bash
# Switch the LG TV on over the network (Wake-on-LAN to TV_MAC), then wait until it answers.
# Needs on the TV: Settings > General > Devices > "TV On With Mobile" / "Turn on via Wi-Fi/LAN" = On
# (the exact menu name differs between firmware versions).
set -euo pipefail
. "$(dirname "$0")/lib.sh"

load_env
require_vars TV_MAC TV_IP WOL_BROADCAST
[ -x "$ROOT/agent/.venv/bin/python" ] || die "run scripts/install-agent.sh first"

cd "$ROOT/agent"
.venv/bin/python - "$TV_MAC" "$WOL_BROADCAST" <<'EOF'
import sys, time
from tvwake.config import normalize_mac
from tvwake.wol import send_magic_packet
mac = normalize_mac(sys.argv[1])
for _ in range(3):
    send_magic_packet(mac, sys.argv[2], (9, 7))
    time.sleep(0.3)
print(f"magic packet sent to {mac}")
EOF

for i in $(seq 1 30); do
    if timeout 1 bash -c "</dev/tcp/$TV_IP/3001" 2>/dev/null; then
        info "TV is on (answered after ${i}s)"
        exit 0
    fi
    sleep 1
done
die "TV did not answer within 30 s. Is 'Turn on via Wi-Fi/LAN' enabled on the TV?"
