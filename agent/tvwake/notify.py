"""Optional Telegram notifications (TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID in .env).

Never raises: a failed notification is logged and otherwise ignored, so it can't break the wake flow
or the auto-sleep watcher.
"""

from __future__ import annotations

import html
import json
import logging
import urllib.error
import urllib.request

from .config import Config

log = logging.getLogger("tvwake.notify")

API = "https://api.telegram.org/bot{token}/sendMessage"


# Color mark in front of each message (same scheme as pc/events.ps1): blue = stream running,
# white = stream stopped, violet = PC power, green = ok, red = error.
MARKS = {"pc.ready": "\U0001F7E2", "pc.error": "\U0001F534"}


def line(icon: str, key: str, detail: str = "") -> str:
    """A message in the bot's terminal style: MARK <code>ICON key</code> details (Telegram HTML)."""
    mark = MARKS.get(key, "")
    return f"{mark} <code>{icon} {key}</code> {html.escape(detail, quote=False)}".strip()


def enabled(cfg: Config) -> bool:
    return bool(cfg.telegram_bot_token and cfg.telegram_chat_id)


def send(cfg: Config, text: str, timeout: float = 10) -> bool:
    """Send `text` as Telegram HTML (build it with line())."""
    if not enabled(cfg):
        return False
    body = json.dumps(
        {"chat_id": cfg.telegram_chat_id, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}
    ).encode()
    req = urllib.request.Request(
        API.format(token=cfg.telegram_bot_token), data=body, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            ok = json.loads(resp.read().decode() or "{}").get("ok") is True
    except (urllib.error.URLError, OSError, ValueError) as e:
        # str(e) never contains the URL (and so not the token)
        log.warning("telegram notification failed: %s", e)
        return False
    if not ok:
        log.warning("telegram refused the notification")
    return ok
