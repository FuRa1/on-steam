import json

import pytest

from tvwake.devmode import DevModeError, Renewer, api_view, parse_lg_reply, parse_remaining

TOKEN = "a" * 64
OK = json.dumps({"result": "success", "errorCode": "200", "errorMsg": "GNL"})
CHECK = json.dumps({"result": "success", "errorCode": "200", "errorMsg": "999:33:05"})


@pytest.mark.parametrize("text,want", [("999:33:05", 999 * 3600 + 33 * 60 + 5), ("1:02", 3720), ("GNL", None), ("", None)])
def test_parse_remaining(text, want):
    assert parse_remaining(text) == want


def test_parse_lg_reply():
    assert parse_lg_reply(200, OK)["errorMsg"] == "GNL"
    with pytest.raises(DevModeError, match="Check sessionToken"):
        parse_lg_reply(200, json.dumps({"result": "fail", "errorCode": "ERR_006", "errorMsg": "Check sessionToken"}))
    with pytest.raises(DevModeError, match="HTTP 500"):
        parse_lg_reply(500, "oops")


class FakeSsh:
    def __init__(self, token=TOKEN, extend_error=None):
        self.token = token
        self.extend_error = extend_error
        self.extends = 0

    def read_token(self):
        if self.token is None:
            raise DevModeError("ssh failed")
        return self.token

    def extend(self):
        if self.extend_error:
            raise DevModeError(self.extend_error)
        self.extends += 1


class World:
    def __init__(self, cfg):
        self.now = 1_700_000_000.0
        self.tv_up = True
        self.urls = []
        self.reset_reply = (200, OK)
        self.ssh = FakeSsh()
        self.renewer = Renewer(cfg, ssh=self.ssh, get=self.get, reachable=lambda: self.tv_up, wall=lambda: self.now)

    def get(self, url):
        self.urls.append(url)
        if "Reset" in url:
            return self.reset_reply
        return 200, CHECK

    def hours(self, h):
        self.now += h * 3600


def test_first_tick_with_tv_on_does_everything(make_cfg):
    cfg = make_cfg()
    w = World(cfg)
    assert w.renewer.tick() == 0
    assert w.ssh.extends == 1
    assert any("ResetDevModeSession" in u and TOKEN in u for u in w.urls)
    assert any("CheckDevModeSession" in u for u in w.urls)
    view = api_view(cfg)
    assert view["remaining_s"] == 999 * 3600 + 33 * 60 + 5
    assert view["expires_at_estimate"] == w.now + view["remaining_s"]
    assert view["last_result"]["ok"] is True
    assert cfg.devmode_token_file.read_text().strip() == TOKEN


def test_second_tick_does_nothing(make_cfg):
    w = World(make_cfg())
    w.renewer.tick()
    w.urls.clear()
    w.hours(0.1)
    assert w.renewer.tick() == 0
    assert w.ssh.extends == 1 and w.urls == []


def test_tv_off_uses_cached_token_for_daily_reset(make_cfg):
    w = World(make_cfg())
    w.renewer.tick()
    w.tv_up = False
    w.ssh.token = None  # ssh would fail anyway
    w.urls.clear()
    w.hours(25)
    assert w.renewer.tick() == 0
    assert any("Reset" in u for u in w.urls)
    assert w.ssh.extends == 1


def test_extend_on_power_on_only_after_interval(make_cfg):
    w = World(make_cfg(DEVMODE_EXTEND_INTERVAL_H=168))
    w.renewer.tick()  # extend #1
    for _ in range(3):  # TV turned off and on a few times within a week
        w.tv_up = False
        w.hours(10)
        w.renewer.tick()
        w.tv_up = True
        w.hours(1)
        w.renewer.tick()
    assert w.ssh.extends == 1
    w.tv_up = False
    w.hours(150)
    w.renewer.tick()
    w.tv_up = True
    w.renewer.tick()  # power-on after > 7 days
    assert w.ssh.extends == 2


def test_extend_when_tv_never_switched_off(make_cfg):
    w = World(make_cfg(DEVMODE_EXTEND_INTERVAL_H=10))
    w.renewer.tick()
    w.hours(15)
    w.renewer.tick()
    assert w.ssh.extends == 1  # not "just on", and < 2x interval
    w.hours(6)
    w.renewer.tick()
    assert w.ssh.extends == 2


def test_failure_returns_nonzero_and_is_recorded(make_cfg):
    cfg = make_cfg()
    w = World(cfg)
    w.reset_reply = (200, json.dumps({"result": "fail", "errorCode": "ERR_006", "errorMsg": "Check sessionToken"}))
    assert w.renewer.tick() == 1
    res = api_view(cfg)["last_result"]
    assert res["ok"] is False and "Check sessionToken" in res["message"]


def test_no_token_ever_is_an_error(make_cfg):
    w = World(make_cfg())
    w.tv_up = False
    assert w.renewer.tick() == 1


def test_force_renew(make_cfg):
    w = World(make_cfg())
    w.renewer.tick()
    w.hours(0.1)
    assert w.renewer.tick(force=True) == 0
    assert w.ssh.extends == 2


def test_state_file_permissions(make_cfg):
    import os
    import stat

    cfg = make_cfg()
    World(cfg).renewer.tick()
    if os.name == "posix":
        assert stat.S_IMODE(cfg.devmode_token_file.stat().st_mode) == 0o600
        assert stat.S_IMODE(cfg.state_dir.stat().st_mode) == 0o700
