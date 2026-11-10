"""Watch the home's public IP (the ISP gives a dynamic one) and report changes to Telegram.

Run by a systemd timer every IPWATCH_EVERY_H hours (`python -m tvwake.ipwatch check`). Each check:

1. Asks public "what is my IP" services (IPv4) for the address. A new address counts only when a
   second service confirms it, so one wrong answer can't fake a change.
2. Compares it with the last one stored in STATE_DIR/ipwatch.json (with a short history).
3. On a change (and on the very first check) sends a Telegram message.
4. If IPWATCH_DNS_HOST is set, also resolves that name and says when it points elsewhere
   (a dynamic DNS record that nobody updated). Said once per address, not on every check.

Exit status is non-zero only when no service answered.
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import logging
import socket
import sys
import time
import urllib.error
import urllib.request
from typing import Callable

from . import notify
from .config import Config, ConfigError, load_from_file
from .devmode import read_state, write_private
from .notify import GREEN, line

log = logging.getLogger("tvwake.ipwatch")

# IPv4-only endpoints that answer with the bare address.
SOURCES = ("https://api.ipify.org", "https://ipv4.icanhazip.com", "https://v4.ident.me")
HISTORY = 20
YELLOW = "\U0001F7E1"


class IpWatchError(RuntimeError):
    pass


def http_ip(url: str, timeout: float = 10) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "tvwake-ipwatch"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read(100).decode("ascii", "replace").strip()


def resolve_v4(host: str) -> list[str]:
    try:
        infos = socket.getaddrinfo(host, None, socket.AF_INET, socket.SOCK_STREAM)
    except socket.gaierror:
        return []
    return sorted({i[4][0] for i in infos})


def _valid(text: str) -> str | None:
    try:
        ip = ipaddress.IPv4Address(text.strip())
    except ValueError:
        return None
    return str(ip) if ip.is_global else None


def ago(seconds: float) -> str:
    m = int(seconds // 60)
    d, h = divmod(m // 60, 24)
    if d:
        return f"{d} d {h} h"
    return f"{h} h {m % 60} min" if h else f"{m} min"


class Watcher:
    def __init__(
        self,
        cfg: Config,
        fetch: Callable[[str], str] = http_ip,
        resolve: Callable[[str], list[str]] = resolve_v4,
        wall: Callable[[], float] = time.time,
        alert: Callable[[str], object] | None = None,
        sources: tuple[str, ...] = SOURCES,
    ) -> None:
        self.cfg = cfg
        self.fetch = fetch
        self.resolve = resolve
        self.wall = wall
        self.alert = alert
        self.sources = sources

    def public_ip(self, known: str | None) -> str:
        """The current public IPv4. A value other than `known` needs two services agreeing."""
        answers: list[str] = []
        for url in self.sources:
            try:
                ip = _valid(self.fetch(url))
            except (urllib.error.URLError, OSError, ValueError) as e:
                log.warning("%s: %s", url, e)
                continue
            if ip is None:
                log.warning("%s: not a public IPv4 address", url)
                continue
            if ip == known or ip in answers:
                return ip
            answers.append(ip)
        if len(self.sources) == 1 and answers:
            return answers[0]
        if answers:
            raise IpWatchError(f"services disagree or only one answered: {', '.join(answers)}")
        raise IpWatchError("no IP service answered (is the internet down?)")

    def _send(self, text: str) -> None:
        if self.alert is None:
            return
        try:
            self.alert(text)
        except Exception:  # a notification must never break the check
            log.exception("ip notification failed")

    def check(self) -> int:
        path = self.cfg.ipwatch_state_file
        st = read_state(path)
        now = self.wall()
        old = st.get("ip")
        st["last_check_at"] = now
        try:
            ip = self.public_ip(old)
        except IpWatchError as e:
            st["last_error"] = {"at": now, "message": str(e)}
            write_private(path, json.dumps(st, indent=2) + "\n")
            log.error("%s", e)
            return 1
        st.pop("last_error", None)

        msg = None
        if ip != old:
            hist = st.setdefault("history", [])
            hist.append({"ip": ip, "since": now})
            del hist[:-HISTORY]
            if old is None:
                log.info("public IP is %s (first check)", ip)
                msg = line("◉", "ip.public", f"{ip} · watching for changes", mark=GREEN)
            else:
                held = f" · the old one held {ago(now - st['since'])}" if st.get("since") else ""
                log.info("public IP changed: %s -> %s", old, ip)
                msg = line("⇄", "ip.changed", f"{old} → {ip}{held}", mark=YELLOW)
            st["ip"], st["since"] = ip, now
            st.pop("dns_warned_for", None)

        host = self.cfg.ipwatch_dns_host
        dns_line = None
        if host:
            got = self.resolve(host)
            st["dns"] = {"host": host, "ips": got, "at": now}
            if ip not in got:
                log.warning("%s resolves to %s, not %s", host, got or "nothing", ip)
                if st.get("dns_warned_for") != ip:
                    detail = f"{host} → {', '.join(got) or 'nothing'}, not {ip} · update the record"
                    dns_line = line("⚠", "dns.stale", detail, mark=YELLOW)
                    st["dns_warned_for"] = ip
            elif st.pop("dns_warned_for", None):
                dns_line = line("✓", "dns.ok", f"{host} → {ip}", mark=GREEN)

        msg = "\n".join(m for m in (msg, dns_line) if m) or None
        if msg:
            self._send(msg)
        else:
            log.info("public IP unchanged: %s (since %s)", ip, ago(now - st.get("since", now)))
        write_private(path, json.dumps(st, indent=2) + "\n")
        return 0


def status(cfg: Config) -> dict:
    return read_state(cfg.ipwatch_state_file)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m tvwake.ipwatch", description="public IP watcher")
    p.add_argument("--env", help="path to the .env file")
    p.add_argument("command", choices=["check", "status"], help="check: look up the IP now; status: print state")
    args = p.parse_args(argv)
    try:
        cfg = load_from_file(args.env)
    except (ConfigError, OSError) as e:
        print(f"config error: {e}", file=sys.stderr)
        return 2
    logging.basicConfig(level=cfg.log_level, format="%(levelname)s %(name)s: %(message)s")
    if args.command == "status":
        print(json.dumps(status(cfg), indent=2))
        return 0
    alert = (lambda text: notify.send(cfg, text)) if notify.enabled(cfg) else None
    return Watcher(cfg, alert=alert).check()


if __name__ == "__main__":
    sys.exit(main())
