#!/usr/bin/env bash
# Create "TV Off" and "TV On" in the macOS Shortcuts app. Each runs scripts/tv-off.sh / tv-on.sh on the
# helper over ssh (like pc/tv-power.cmd on Windows). Shortcuts asks you to confirm each import ("Add Shortcut").
# Needs: Shortcuts > Settings > Advanced > "Allow Running Scripts", passwordless ssh to the helper.
# The helper's ssh alias defaults to "l590"; override with TVWAKE_HELPER_SSH.
# Usage: mac/make-tv-shortcuts.sh [off|on ...]     (default: off on)
set -euo pipefail

HELPER="${TVWAKE_HELPER_SSH:-l590}"
OUT="$(mktemp -d)"
trap 'sleep 5; rm -rf "$OUT"' EXIT   # give Shortcuts time to read the files
[ $# -gt 0 ] || set -- off on

for action in "$@"; do
    case "$action" in
        off) name="TV Off"; color=4282601983 ;;   # red
        on)  name="TV On";  color=431817727 ;;    # green
        *) echo "unknown action: $action" >&2; exit 2 ;;
    esac
    script="ssh -o BatchMode=yes -o ConnectTimeout=5 $HELPER 'cd ~/tv-wake-launcher && bash scripts/tv-$action.sh'"
    python3 - "$OUT/$action.unsigned.shortcut" "$name" "$script" "$color" <<'EOF'
import plistlib, sys
path, name, script, color = sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4])
wf = {
    "WFWorkflowMinimumClientVersion": 900,
    "WFWorkflowMinimumClientVersionString": "900",
    "WFWorkflowClientVersion": "2302.0.4",
    "WFWorkflowIcon": {"WFWorkflowIconStartColor": color, "WFWorkflowIconGlyphNumber": 59511},
    "WFWorkflowImportQuestions": [],
    "WFWorkflowInputContentItemClasses": [],
    "WFWorkflowTypes": [],
    "WFWorkflowHasShortcutInputVariables": False,
    "WFWorkflowActions": [
        {"WFWorkflowActionIdentifier": "is.workflow.actions.runshellscript",
         "WFWorkflowActionParameters": {"Script": script, "Shell": "/bin/zsh", "InputMode": "to stdin"}},
        {"WFWorkflowActionIdentifier": "is.workflow.actions.notification",
         "WFWorkflowActionParameters": {"WFNotificationActionTitle": name, "WFNotificationActionBody": "Done"}},
    ],
}
with open(path, "wb") as f:
    plistlib.dump(wf, f, fmt=plistlib.FMT_BINARY)
EOF
    shortcuts sign --mode anyone --input "$OUT/$action.unsigned.shortcut" --output "$OUT/$name.shortcut"
    open "$OUT/$name.shortcut"
    echo "opened $name: click \"Add Shortcut\" in Shortcuts"
done
