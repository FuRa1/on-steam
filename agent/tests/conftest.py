import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tvwake.config import load_config  # noqa: E402

SECRET = "x" * 64


def make_env(**overrides) -> dict:
    env = {
        "HELPER_HOST": "127.0.0.1",
        "SHARED_SECRET": SECRET,
        "PC_IP": "10.0.0.5",
        "PC_MAC": "AA-BB-CC-DD-EE-FF",
        "WOL_BROADCAST": "10.0.0.255",
        "QUIET_PERIOD_S": "30",
        "POLL_INTERVAL_S": "4",
        "BOOT_TIMEOUT_S": "150",
        "STREAM_TIMEOUT_S": "90",
        "WOL_RESEND_S": "20",
        "TV_IP": "10.0.0.9",
    }
    env.update({k: str(v) for k, v in overrides.items()})
    return env


@pytest.fixture
def make_cfg(tmp_path):
    def _make(**overrides):
        overrides.setdefault("STATE_DIR", str(tmp_path / "state"))
        return load_config(make_env(**overrides))

    return _make
