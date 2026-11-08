"""Configuration: everything device-specific comes from the .env file.

The .env format is deliberately simple so the same file works for bash
(`set -a; . ./.env`) and for this parser:

    KEY=value
    KEY="value with spaces"
    # full-line comments only (no inline comments)
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

_LINE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$")
_MAC = re.compile(r"^[0-9A-Fa-f]{2}([:-]?)[0-9A-Fa-f]{2}(\1[0-9A-Fa-f]{2}){4}$")

# Defaults per streaming backend: (port that answers when the server is up, TV app id, title)
BACKENDS = {
    "moonlight": (47989, "com.limelight.webos", "Moonlight"),
    "steamlink": (27036, "com.valvesoftware.steamlink", "Steam Link"),
}


class ConfigError(ValueError):
    pass


def parse_env_file(path: str | Path) -> dict[str, str]:
    """Parse a KEY=value file. Raises ConfigError with the line number on bad lines."""
    result: dict[str, str] = {}
    for lineno, raw in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = _LINE.match(line)
        if not m:
            raise ConfigError(f"{path}:{lineno}: cannot parse line: {raw!r}")
        key, value = m.group(1), m.group(2)
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        result[key] = value
    return result


def _expand(p: str) -> Path:
    return Path(os.path.expandvars(os.path.expanduser(p)))


@dataclass(frozen=True)
class Config:
    # helper / API
    helper_host: str
    helper_port: int
    helper_bind: str
    shared_secret: str
    # PC
    pc_ip: str
    pc_mac: str
    wol_broadcast: str
    wol_ports: tuple[int, ...]
    wol_repeat: int
    wol_resend_s: float
    # streaming backend
    stream_backend: str
    stream_port: int
    stream_app_id: str
    stream_app_title: str
    # timings
    quiet_period_s: float
    poll_interval_s: float
    boot_timeout_s: float
    stream_timeout_s: float
    # mock
    mock_pc: bool
    mock_boot_s: float
    mock_stream_s: float
    mock_fail: str  # "", "boot" or "stream"
    mock_initially_on: bool
    mock_auto_off_s: float
    # TV / dev mode
    tv_ip: str
    tv_ssh_port: int
    tv_ssh_user: str
    tv_ssh_key: Path
    devmode_renew_url: str
    devmode_check_url: str
    devmode_renew_interval_h: float
    devmode_extend_interval_h: float
    devmode_check_interval_h: float
    state_dir: Path
    log_level: str
    raw: dict[str, str] = field(repr=False, default_factory=dict)

    @property
    def devmode_state_file(self) -> Path:
        return self.state_dir / "devmode.json"

    @property
    def devmode_token_file(self) -> Path:
        return self.state_dir / "devmode_token"


def _get(env: dict[str, str], key: str, default: str | None = None) -> str:
    val = env.get(key, "")
    if val == "":
        if default is None:
            raise ConfigError(f"missing required setting {key}")
        return default
    return val


def _num(env: dict[str, str], key: str, default: str, *, minimum: float = 0) -> float:
    raw = _get(env, key, default)
    try:
        val = float(raw)
    except ValueError:
        raise ConfigError(f"{key} must be a number, got {raw!r}") from None
    if val < minimum:
        raise ConfigError(f"{key} must be >= {minimum}, got {val}")
    return val


def _int(env: dict[str, str], key: str, default: str, *, minimum: int = 0) -> int:
    val = _num(env, key, default, minimum=minimum)
    if val != int(val):
        raise ConfigError(f"{key} must be a whole number")
    return int(val)


def _bool(env: dict[str, str], key: str, default: str) -> bool:
    raw = _get(env, key, default).strip().lower()
    if raw in ("1", "true", "yes", "on"):
        return True
    if raw in ("0", "false", "no", "off"):
        return False
    raise ConfigError(f"{key} must be 0/1, got {raw!r}")


def normalize_mac(mac: str) -> str:
    if not _MAC.match(mac.strip()):
        raise ConfigError(f"PC_MAC is not a valid MAC address: {mac!r}")
    hexdigits = re.sub(r"[^0-9A-Fa-f]", "", mac)
    return ":".join(hexdigits[i : i + 2] for i in range(0, 12, 2)).lower()


def load_config(env: dict[str, str]) -> Config:
    """Build a validated Config from a dict of settings (from .env and/or os.environ)."""
    mock = _bool(env, "MOCK_PC", "0")

    secret = _get(env, "SHARED_SECRET")
    if len(secret) < 32:
        raise ConfigError("SHARED_SECRET must be at least 32 characters (run scripts/gen-secret.sh)")

    backend = _get(env, "STREAM_BACKEND", "moonlight").lower()
    if backend not in BACKENDS:
        raise ConfigError(f"STREAM_BACKEND must be one of {sorted(BACKENDS)}, got {backend!r}")
    def_port, def_app, def_title = BACKENDS[backend]

    ports_raw = _get(env, "WOL_PORTS", "9,7")
    try:
        wol_ports = tuple(int(p) for p in ports_raw.split(",") if p.strip())
    except ValueError:
        raise ConfigError(f"WOL_PORTS must be comma-separated numbers, got {ports_raw!r}") from None
    if not wol_ports:
        raise ConfigError("WOL_PORTS is empty")

    mock_fail = _get(env, "MOCK_FAIL", "none").lower()
    if mock_fail not in ("none", "boot", "stream"):
        raise ConfigError("MOCK_FAIL must be none, boot or stream")

    helper_host = _get(env, "HELPER_HOST")
    # In mock mode the PC settings are not used, so allow them to be absent.
    pc_default = "127.0.0.1" if mock else None
    mac_default = "00:00:00:00:00:00" if mock else None

    return Config(
        helper_host=helper_host,
        helper_port=_int(env, "HELPER_PORT", "8787", minimum=1),
        helper_bind=_get(env, "HELPER_BIND", helper_host),
        shared_secret=secret,
        pc_ip=_get(env, "PC_IP", pc_default),
        pc_mac=normalize_mac(_get(env, "PC_MAC", mac_default)),
        wol_broadcast=_get(env, "WOL_BROADCAST", "255.255.255.255"),
        wol_ports=wol_ports,
        wol_repeat=_int(env, "WOL_REPEAT", "3", minimum=1),
        wol_resend_s=_num(env, "WOL_RESEND_S", "20"),
        stream_backend=backend,
        stream_port=_int(env, "STREAM_PORT", str(def_port), minimum=1),
        stream_app_id=_get(env, "STREAM_APP_ID", def_app),
        stream_app_title=_get(env, "STREAM_APP_TITLE", def_title),
        quiet_period_s=_num(env, "QUIET_PERIOD_S", "30"),
        poll_interval_s=_num(env, "POLL_INTERVAL_S", "4", minimum=0.1),
        boot_timeout_s=_num(env, "BOOT_TIMEOUT_S", "150", minimum=1),
        stream_timeout_s=_num(env, "STREAM_TIMEOUT_S", "90", minimum=1),
        mock_pc=mock,
        mock_boot_s=_num(env, "MOCK_BOOT_S", "20"),
        mock_stream_s=_num(env, "MOCK_STREAM_S", "10"),
        mock_fail="" if mock_fail == "none" else mock_fail,
        mock_initially_on=_bool(env, "MOCK_INITIALLY_ON", "0"),
        mock_auto_off_s=_num(env, "MOCK_AUTO_OFF_S", "120"),
        tv_ip=_get(env, "TV_IP", ""),
        tv_ssh_port=_int(env, "TV_SSH_PORT", "9922", minimum=1),
        tv_ssh_user=_get(env, "TV_SSH_USER", "prisoner"),
        tv_ssh_key=_expand(_get(env, "TV_SSH_KEY", "~/.ssh/tvwake_tv")),
        devmode_renew_url=_get(
            env,
            "DEVMODE_RENEW_URL",
            "https://developer.lge.com/secure/ResetDevModeSession.dev?sessionToken={token}",
        ),
        devmode_check_url=_get(
            env,
            "DEVMODE_CHECK_URL",
            "https://developer.lge.com/secure/CheckDevModeSession.dev?sessionToken={token}",
        ),
        devmode_renew_interval_h=_num(env, "DEVMODE_RENEW_INTERVAL_H", "24"),
        devmode_extend_interval_h=_num(env, "DEVMODE_EXTEND_INTERVAL_H", "168"),
        devmode_check_interval_h=_num(env, "DEVMODE_CHECK_INTERVAL_H", "6"),
        state_dir=_expand(_get(env, "STATE_DIR", "~/.local/state/tvwake")),
        log_level=_get(env, "LOG_LEVEL", "INFO").upper(),
        raw=dict(env),
    )


def load_from_file(path: str | Path | None) -> Config:
    """Load .env (if given) and let real environment variables override it."""
    env: dict[str, str] = parse_env_file(path) if path else {}
    # Environment variables win, e.g. `MOCK_PC=1 python -m tvwake ...` for a quick test.
    env.update({k: v for k, v in os.environ.items() if k in KNOWN_KEYS})
    return load_config(env)


KNOWN_KEYS = frozenset(
    """HELPER_HOST HELPER_PORT HELPER_BIND SHARED_SECRET PC_IP PC_MAC WOL_BROADCAST WOL_PORTS
    WOL_REPEAT WOL_RESEND_S STREAM_BACKEND STREAM_PORT STREAM_APP_ID STREAM_APP_TITLE
    QUIET_PERIOD_S POLL_INTERVAL_S BOOT_TIMEOUT_S STREAM_TIMEOUT_S MOCK_PC MOCK_BOOT_S
    MOCK_STREAM_S MOCK_FAIL MOCK_INITIALLY_ON MOCK_AUTO_OFF_S TV_IP TV_SSH_PORT TV_SSH_USER TV_SSH_KEY
    DEVMODE_RENEW_URL DEVMODE_CHECK_URL DEVMODE_RENEW_INTERVAL_H DEVMODE_EXTEND_INTERVAL_H
    DEVMODE_CHECK_INTERVAL_H STATE_DIR LOG_LEVEL""".split()
)
