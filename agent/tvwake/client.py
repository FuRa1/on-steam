"""Command-line client for the agent API: does what the TV app does, from a terminal.

    python -m tvwake.client --env ../.env health
    python -m tvwake.client --env ../.env wake      # start/join a wake job and follow it
    python -m tvwake.client --env ../.env devmode

Useful to test the whole wake flow without the TV, and as a reference for the request
signing (same algorithm as tv-app/js/api.js).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request

from .auth import sign
from .config import ConfigError, load_from_file


class Client:
    def __init__(self, base: str, secret: str) -> None:
        self.base = base.rstrip("/")
        self.secret = secret

    def call(self, method: str, path: str, auth: bool = True) -> tuple[int, dict]:
        headers = {"X-Client": "tvwake-cli"}
        if auth:
            ts = str(int(time.time()))
            headers.update({"X-Timestamp": ts, "X-Signature": sign(self.secret, method, path, ts, b"")})
        req = urllib.request.Request(self.base + path, method=method, headers=headers, data=b"" if method == "POST" else None)
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")


def follow(c: Client) -> int:
    status, data = c.call("POST", "/wake")
    if status != 200:
        print(f"wake failed: HTTP {status} {data}")
        return 1
    job_id = data["job_id"]
    print(f"job {job_id}")
    shown: dict[str, str] = {}
    while True:
        status, job = c.call("GET", f"/jobs/{job_id}")
        if status != 200:
            print(f"poll failed: HTTP {status} {job}")
            return 1
        for st in job["stages"]:
            line = f"{st['state']:8} {st['title']} — {st['message']}"
            if shown.get(st["id"]) != line:
                el = st.get("elapsed_s")
                print(f"  [{el if el is not None else '-':>6}s] {line}")
                shown[st["id"]] = line
        if job["state"] != "running":
            print(f"result: {job['state']} {job.get('error') or ''}")
            return 0 if job["state"] == "ready" else 1
        time.sleep(2)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m tvwake.client")
    p.add_argument("--env")
    p.add_argument("--url", help="agent URL (default http://HELPER_HOST:HELPER_PORT)")
    p.add_argument("command", choices=["health", "wake", "devmode"])
    args = p.parse_args(argv)
    try:
        cfg = load_from_file(args.env)
    except (ConfigError, OSError) as e:
        print(f"config error: {e}", file=sys.stderr)
        return 2
    c = Client(args.url or f"http://{cfg.helper_host}:{cfg.helper_port}", cfg.shared_secret)
    try:
        if args.command == "wake":
            return follow(c)
        status, data = c.call("GET", f"/{args.command}", auth=args.command != "health")
        print(json.dumps(data, indent=2))
        return 0 if status == 200 else 1
    except (urllib.error.URLError, OSError) as e:
        print(f"cannot reach the agent: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
