#!/usr/bin/env python3
"""Generate tv-app/config.js and tv-app/appinfo.json from their templates and the environment.

Called by build-tv.sh after it has loaded .env. Values are JSON-encoded, so quotes or
backslashes in .env cannot break the generated JavaScript.
"""

import json
import os
import sys
from pathlib import Path

BACKENDS = {
    "moonlight": ("com.limelight.webos", "Moonlight"),
    "steamlink": ("com.valvesoftware.steamlink", "Steam Link"),
}


def env(key, default=None):
    val = os.environ.get(key, "")
    if val == "":
        if default is None:
            sys.exit(f"ERROR: {key} is not set in .env")
        return default
    return val


def num(key, default):
    try:
        return float(env(key, str(default)))
    except ValueError:
        sys.exit(f"ERROR: {key} must be a number")


def main(app_dir: Path) -> None:
    backend = env("STREAM_BACKEND", "moonlight").lower()
    if backend not in BACKENDS:
        sys.exit(f"ERROR: STREAM_BACKEND must be one of {sorted(BACKENDS)}")
    def_id, def_title = BACKENDS[backend]
    try:
        params = json.loads(env("STREAM_APP_PARAMS", "{}"))
    except ValueError:
        sys.exit("ERROR: STREAM_APP_PARAMS must be JSON, e.g. {} ")

    # Longest the TV waits before giving up, a bit more than the helper's own timeouts.
    max_wait = num("BOOT_TIMEOUT_S", 150) + num("STREAM_TIMEOUT_S", 90) + 60

    config = {
        "appId": env("APP_ID"),
        "appTitle": env("APP_TITLE"),
        "pcName": env("PC_NAME", "the PC"),
        "version": env("APP_VERSION", "1.0.0"),
        "helperUrl": f"http://{env('HELPER_HOST')}:{env('HELPER_PORT', '8787')}",
        "secret": env("SHARED_SECRET"),
        "streamAppId": env("STREAM_APP_ID", def_id),
        "streamAppTitle": env("STREAM_APP_TITLE", def_title),
        "streamAppParams": params,
        "pollMs": int(num("TV_POLL_MS", 1500)),
        "maxWaitS": int(max_wait),
        "closeAfterLaunch": env("TV_CLOSE_AFTER_LAUNCH", "1") not in ("0", "false", "no"),
    }
    if len(config["secret"]) < 32:
        sys.exit("ERROR: SHARED_SECRET is too short; run scripts/gen-secret.sh")

    tpl = (app_dir / "config.js.template").read_text(encoding="utf-8")
    (app_dir / "config.js").write_text(tpl.replace("@CONFIG_JSON@", json.dumps(config, indent=2)), encoding="utf-8")

    subst = {"@APP_ID@": env("APP_ID"), "@APP_TITLE@": env("APP_TITLE"), "@APP_VERSION@": env("APP_VERSION", "1.0.0")}
    info = json.loads((app_dir / "appinfo.json.template").read_text(encoding="utf-8"))
    for k, v in info.items():
        if isinstance(v, str) and v in subst:
            info[k] = subst[v]
    (app_dir / "appinfo.json").write_text(json.dumps(info, indent=2) + "\n", encoding="utf-8")
    print(f"generated config.js and appinfo.json for {info['id']} {info['version']} -> {config['helperUrl']}")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
