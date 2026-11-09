"""Keep the TV's Developer Mode session alive.

Run every few minutes by a systemd timer (`python -m tvwake.devmode tick`). Each tick:

1. Checks whether the TV is reachable (TCP connect to the dev mode SSH port).
2. If it is: reads the session token over SSH and caches it (the token lets us renew
   while the TV is off).
3. "Extend" on the TV, the same as pressing *Extend Session Time* in the Developer Mode app:
   launch com.palmdts.devmode with {"extend": true} via luna-send-pub. Done when the TV has
   just become reachable and the last extend is older than DEVMODE_EXTEND_INTERVAL_H, or when
   the last extend is older than twice that (TV that is never switched off).
4. Cloud reset: GET DEVMODE_RENEW_URL with the (cached) token, every DEVMODE_RENEW_INTERVAL_H.
   This works while the TV is off; per webosbrew the TV only re-reads it at boot.
5. Asks LG for the remaining session time (DEVMODE_CHECK_URL) and stores it.

Exit status is non-zero only if something was attempted and failed, so systemd marks
only real failures.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from pathlib import Path
from typing import Callable

from . import notify
from .config import Config, ConfigError, load_from_file
from .notify import GREEN, line

log = logging.getLogger("tvwake.devmode")

TOKEN_PATH_ON_TV = "/var/luna/preferences/devmode_enabled"
EXTEND_PAYLOAD = '{"id":"com.palmdts.devmode","params":{"extend":true}}'
_TOKEN_RE = re.compile(r"^[A-Za-z0-9_\-]{16,256}$")


LOW_REMAINING_H = 100  # warn when the session has less than this left
ALERT_EVERY_H = 12  # repeat a dev mode warning at most this often


class DevModeError(Exception):
    pass


# ---------------------------------------------------------------------------
# Talking to the TV and to LG
# ---------------------------------------------------------------------------


class TvSsh:
    """Runs commands on the TV over SSH as the dev mode user ('prisoner')."""

    def __init__(self, cfg: Config, run: Callable = subprocess.run) -> None:
        self.cfg = cfg
        self._run = run

    def _cmd(self, remote: str, tty: bool = False) -> list[str]:
        c = self.cfg
        cmd = [
            "ssh", "-i", str(c.tv_ssh_key), "-p", str(c.tv_ssh_port),
            "-o", "BatchMode=yes",
            "-o", "ConnectTimeout=5",
            "-o", "StrictHostKeyChecking=accept-new",
            # The TV's SSH server only offers old RSA algorithms.
            "-o", "PubkeyAcceptedAlgorithms=+ssh-rsa",
            "-o", "HostKeyAlgorithms=+ssh-rsa",
        ]
        if tty:
            cmd.append("-tt")  # luna-send-pub prints its reply only when it has a terminal
        return cmd + [f"{c.tv_ssh_user}@{c.tv_ip}", remote]

    def _exec(self, remote: str, tty: bool = False) -> str:
        try:
            res = self._run(
                self._cmd(remote, tty), stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=30
            )
        except (OSError, subprocess.TimeoutExpired) as e:
            raise DevModeError(f"ssh to TV failed: {e}") from e
        if res.returncode != 0:
            raise DevModeError(f"ssh to TV failed (exit {res.returncode}): {(res.stderr or '').strip()[:300]}")
        return res.stdout

    def read_token(self) -> str:
        token = self._exec(f"cat {TOKEN_PATH_ON_TV}").strip()
        if not _TOKEN_RE.match(token):
            raise DevModeError("token file on the TV is empty or has an unexpected format")
        return token

    def extend(self) -> None:
        out = self._exec(f"/usr/bin/luna-send-pub -n 1 luna://com.webos.applicationManager/launch '{EXTEND_PAYLOAD}'", tty=True)
        reply = _first_json(out)
        if not reply or reply.get("returnValue") is not True:
            raise DevModeError(f"extend was refused by the TV: {out.strip()[:300]}")


def _first_json(text: str) -> dict | None:
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("{"):
            try:
                return json.loads(line)
            except ValueError:
                continue
    return None


def http_get(url: str, timeout: float = 15) -> tuple[int, str]:
    req = urllib.request.Request(url, headers={"User-Agent": "tv-wake-launcher"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read(4096).decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read(4096).decode("utf-8", "replace")
    except (urllib.error.URLError, OSError) as e:
        raise DevModeError(f"request to LG failed: {e}") from e


def parse_lg_reply(status: int, body: str) -> dict:
    """LG answers {"result":"success","errorCode":"200","errorMsg":"..."}; anything else is a failure."""
    try:
        data = json.loads(body)
    except ValueError:
        data = None
    if status != 200 or not isinstance(data, dict) or data.get("result") != "success":
        detail = data.get("errorMsg") if isinstance(data, dict) else body.strip()[:200]
        raise DevModeError(f"LG server said no (HTTP {status}): {detail}")
    return data


def parse_remaining(text: str) -> int | None:
    """'999:33:05' (hours:minutes:seconds) -> seconds. None if the format is unknown."""
    m = re.fullmatch(r"\s*(\d+):(\d{1,2})(?::(\d{1,2}))?\s*", text or "")
    if not m:
        return None
    h, mnt, s = int(m.group(1)), int(m.group(2)), int(m.group(3) or 0)
    return h * 3600 + mnt * 60 + s


def tcp_reachable(host: str, port: int, timeout: float = 3) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


# ---------------------------------------------------------------------------
# State file
# ---------------------------------------------------------------------------


def read_state(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as e:
        log.warning("cannot read %s (%s); starting fresh", path, e)
        return {}


def write_private(path: Path, text: str) -> None:
    """Atomic write with mode 600 (the state dir also holds the token)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


@contextmanager
def _lock(state_dir: Path):
    """Prevent a manual `renew` and the timer from running at the same time."""
    state_dir.mkdir(parents=True, exist_ok=True)
    try:
        import fcntl
    except ImportError:  # not on Linux (tests on other OSes)
        yield
        return
    with open(state_dir / "devmode.lock", "w") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def api_view(cfg: Config) -> dict:
    """What GET /devmode returns."""
    st = read_state(cfg.devmode_state_file)
    return {
        "last_renewed_at": st.get("last_renewed_at"),
        "expires_at_estimate": st.get("expires_at_estimate"),
        "remaining_s": st.get("remaining_s"),
        "checked_at": st.get("last_check_at"),
        "tv_reachable": st.get("tv_was_up"),
        "last_result": st.get("last_result"),
    }


# ---------------------------------------------------------------------------
# The tick
# ---------------------------------------------------------------------------


class Renewer:
    def __init__(
        self,
        cfg: Config,
        ssh: TvSsh | None = None,
        get: Callable[[str], tuple[int, str]] = http_get,
        reachable: Callable[[], bool] | None = None,
        wall: Callable[[], float] = time.time,
        alert: Callable[[str], object] | None = None,
    ) -> None:
        self.cfg = cfg
        self.ssh = ssh or TvSsh(cfg)
        self.get = get
        self.reachable = reachable or (lambda: bool(cfg.tv_ip) and tcp_reachable(cfg.tv_ip, cfg.tv_ssh_port))
        self.wall = wall
        self.alert = alert  # Telegram (notify.send) or None

    def _maybe_alert(self, st: dict, now: float, errors: list[str], done: list[str]) -> None:
        """Telegram: renewal failing or time running low (at most every ALERT_EVERY_H), and once when fixed."""
        if self.alert is None:
            return
        remaining = st.get("remaining_s")
        left = f"{int(remaining // 3600)} h left" if remaining is not None else "time left unknown"  # floor: 999:33 is not 1000 h
        problem = None
        if errors:
            problem = f"{'; '.join(errors)[:300]} · {left}"
        elif remaining is not None and remaining < LOW_REMAINING_H * 3600:
            problem = f"only {left} · is renewal working?"
        if problem:
            if self._older(st.get("last_alert_at"), ALERT_EVERY_H, now):
                self._send(line("✕", "tv.devmode", problem))
                st["last_alert_at"] = now
            st["alert_active"] = True
        elif st.get("alert_active") and done:
            self._send(line("✓", "tv.devmode", f"renewed again · {left}", mark=GREEN))
            st["alert_active"] = False
            st.pop("last_alert_at", None)

    def _send(self, text: str) -> None:
        try:
            self.alert(text)
        except Exception:  # a notification must never break the renewal
            log.exception("dev mode notification failed")

    def _older(self, ts: float | None, hours: float, now: float) -> bool:
        return ts is None or now - ts >= hours * 3600

    def _url(self, template: str, token: str) -> str:
        return template.replace("{token}", urllib.request.quote(token, safe=""))

    def _cached_token(self) -> str | None:
        try:
            tok = self.cfg.devmode_token_file.read_text(encoding="utf-8").strip()
            return tok if _TOKEN_RE.match(tok) else None
        except OSError:
            return None

    def tick(self, force: bool = False) -> int:
        cfg = self.cfg
        with _lock(cfg.state_dir):
            st = read_state(cfg.devmode_state_file)
            now = self.wall()
            done: list[str] = []
            errors: list[str] = []

            tv_up = self.reachable()
            just_on = tv_up and not st.get("tv_was_up")
            if just_on:
                log.info("TV is reachable (was not before)")

            token = None
            if tv_up:
                try:
                    token = self.ssh.read_token()
                    if token != self._cached_token():
                        write_private(cfg.devmode_token_file, token + "\n")
                        log.info("cached a new dev mode token")
                except DevModeError as e:
                    errors.append(f"read token: {e}")

                last_ext = st.get("last_extend_at")
                interval = cfg.devmode_extend_interval_h
                if force or (just_on and self._older(last_ext, interval, now)) or self._older(last_ext, 2 * interval, now):
                    try:
                        self.ssh.extend()
                        st["last_extend_at"] = st["last_renewed_at"] = now
                        done.append("extend")
                        log.info("extended the session on the TV")
                    except DevModeError as e:
                        errors.append(f"extend: {e}")

            token = token or self._cached_token()

            if force or self._older(st.get("last_reset_at"), cfg.devmode_renew_interval_h, now):
                if token:
                    try:
                        parse_lg_reply(*self.get(self._url(cfg.devmode_renew_url, token)))
                        st["last_reset_at"] = st["last_renewed_at"] = now
                        done.append("reset")
                        log.info("cloud reset accepted by LG")
                    except DevModeError as e:
                        errors.append(f"cloud reset: {e}")
                else:
                    errors.append("cloud reset: no token yet (the TV must be on once so it can be read)")

            if token and (done or force or self._older(st.get("last_check_at"), cfg.devmode_check_interval_h, now)):
                try:
                    reply = parse_lg_reply(*self.get(self._url(cfg.devmode_check_url, token)))
                    remaining = parse_remaining(str(reply.get("errorMsg", "")))
                    st["last_check_at"] = now
                    st["remaining_s"] = remaining
                    st["expires_at_estimate"] = now + remaining if remaining is not None else None
                    done.append("check")
                    log.info("LG reports %s remaining", reply.get("errorMsg"))
                except DevModeError as e:
                    errors.append(f"check: {e}")

            self._maybe_alert(st, now, errors, done)
            st["tv_was_up"] = tv_up
            st["last_tick_at"] = now
            if done or errors:
                st["last_result"] = {
                    "at": now,
                    "ok": not errors,
                    "actions": done,
                    "message": "; ".join(errors) if errors else "ok",
                }
            write_private(cfg.devmode_state_file, json.dumps(st, indent=2) + "\n")

        for e in errors:
            log.error("%s", e)
        if not done and not errors:
            log.debug("nothing to do (tv %s)", "up" if tv_up else "down")
        return 1 if errors else 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m tvwake.devmode", description="LG Developer Mode session keeper")
    p.add_argument("--env", help="path to the .env file")
    p.add_argument("command", choices=["tick", "renew", "status"], help="tick: do what is due; renew: do everything now; status: print state")
    args = p.parse_args(argv)
    try:
        cfg = load_from_file(args.env)
    except (ConfigError, OSError) as e:
        print(f"config error: {e}", file=sys.stderr)
        return 2
    logging.basicConfig(level=cfg.log_level, format="%(levelname)s %(name)s: %(message)s")
    if args.command == "status":
        print(json.dumps(api_view(cfg), indent=2))
        return 0
    if not cfg.tv_ip:
        log.error("TV_IP is not set")
        return 2
    alert = (lambda text: notify.send(cfg, text)) if notify.enabled(cfg) else None
    return Renewer(cfg, alert=alert).tick(force=args.command == "renew")


if __name__ == "__main__":
    sys.exit(main())
