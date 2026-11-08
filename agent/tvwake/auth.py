"""Request signing shared with the TV app.

signature = hex(HMAC-SHA256(secret, METHOD + "\\n" + PATH + "\\n" + TIMESTAMP + "\\n" + BODY))

TIMESTAMP is whole seconds since the Unix epoch. PATH is the URL path without the query string.
Requests are rejected if the timestamp is more than `window_s` away from the helper's clock,
or if the same signature was already used (replay).
"""

from __future__ import annotations

import hashlib
import hmac
import threading
from typing import Callable


def sign(secret: str, method: str, path: str, timestamp: str, body: bytes | str = b"") -> str:
    if isinstance(body, str):
        body = body.encode("utf-8")
    msg = "\n".join([method.upper(), path, timestamp]).encode("utf-8") + b"\n" + body
    return hmac.new(secret.encode("utf-8"), msg, hashlib.sha256).hexdigest()


class AuthError(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class Verifier:
    def __init__(self, secret: str, wall: Callable[[], float], window_s: int = 60) -> None:
        self._secret = secret
        self._wall = wall
        self._window = window_s
        self._seen: dict[str, float] = {}  # signature -> time after which it can be forgotten
        self._lock = threading.Lock()

    def verify(self, method: str, path: str, timestamp: str | None, signature: str | None, body: bytes) -> None:
        if not timestamp or not signature:
            raise AuthError("missing X-Timestamp or X-Signature header")
        try:
            ts = int(timestamp)
        except ValueError:
            raise AuthError("bad timestamp") from None
        now = self._wall()
        if abs(now - ts) > self._window:
            raise AuthError("timestamp outside allowed window (check TV clock)")
        expected = sign(self._secret, method, path, timestamp, body)
        if not hmac.compare_digest(expected, signature.strip().lower()):
            raise AuthError("bad signature")
        with self._lock:
            self._prune(now)
            if signature in self._seen:
                raise AuthError("replayed request")
            # A signature can only be valid while its timestamp is inside the window.
            self._seen[signature] = ts + self._window + 1

    def _prune(self, now: float) -> None:
        for s in [s for s, until in self._seen.items() if until < now]:
            del self._seen[s]
