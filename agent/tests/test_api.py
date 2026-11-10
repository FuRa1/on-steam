import time

from fastapi.testclient import TestClient

from tvwake.api import create_app
from tvwake.auth import sign
from tvwake.clock import Clock
from tvwake.mock import MockPC
from tvwake.wake import WakeManager

from .conftest import SECRET


def client(make_cfg):
    cfg = make_cfg(MOCK_PC=1)
    clock = Clock()
    pc = MockPC(clock, boot_s=0, stream_s=0, initially_on=True)
    return TestClient(create_app(cfg, WakeManager(cfg, pc, clock)))


def headers(method, path, body=b"", ts=None):
    ts = str(int(ts if ts is not None else time.time()))
    return {"X-Timestamp": ts, "X-Signature": sign(SECRET, method, path, ts, body)}


def test_health_is_public(make_cfg):
    r = client(make_cfg).get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True and body["mock"] is True and "server_time" in body


def test_wake_requires_auth(make_cfg):
    c = client(make_cfg)
    assert c.post("/wake").status_code == 401
    r = c.post("/wake", headers=headers("POST", "/wake", ts=time.time() - 300))
    assert r.status_code == 401
    assert "server_time" in r.json()["detail"]


def test_wake_and_poll(make_cfg):
    c = client(make_cfg)
    r = c.post("/wake", headers=headers("POST", "/wake"))
    assert r.status_code == 200
    job_id = r.json()["job_id"]
    path = f"/jobs/{job_id}"
    for _ in range(50):
        j = c.get(path, headers=headers("GET", path)).json()
        if j["state"] != "running":
            break
        time.sleep(0.05)
    assert j["state"] == "ready"
    assert [s["id"] for s in j["stages"]] == ["pc_up", "wake", "boot", "stream"]


def test_replay_rejected(make_cfg):
    c = client(make_cfg)
    h = headers("POST", "/wake")
    assert c.post("/wake", headers=h).status_code == 200
    assert c.post("/wake", headers=h).status_code == 401


def test_unknown_job_404(make_cfg):
    c = client(make_cfg)
    assert c.get("/jobs/nope", headers=headers("GET", "/jobs/nope")).status_code == 404


def test_devmode_empty_state(make_cfg):
    c = client(make_cfg)
    r = c.get("/devmode", headers=headers("GET", "/devmode"))
    assert r.status_code == 200
    assert r.json()["last_renewed_at"] is None


def test_cors_preflight_from_file_origin(make_cfg):
    c = client(make_cfg)
    r = c.options(
        "/wake",
        headers={
            "Origin": "null",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "x-timestamp,x-signature",
        },
    )
    assert r.status_code == 200
    assert r.headers["access-control-allow-origin"] in ("*", "null")
    assert "x-signature" in r.headers["access-control-allow-headers"].lower()


def test_shutdown_requires_auth(make_cfg):
    c = client(make_cfg)
    assert c.post("/shutdown").status_code == 401
    assert c.get("/pc").status_code == 401


def test_shutdown_mock_pc(make_cfg):
    c = client(make_cfg)
    assert c.get("/pc", headers=headers("GET", "/pc")).json() == {"up": True}
    r = c.post("/shutdown", headers=headers("POST", "/shutdown"))
    assert r.status_code == 200 and r.json() == {"state": "sent"}
    # a different timestamp: the same request signed in the same second is rejected as a replay
    assert c.get("/pc", headers=headers("GET", "/pc", ts=time.time() - 1)).json() == {"up": False}
    # pressing it again: the PC is off already
    r = c.post("/shutdown", headers=headers("POST", "/shutdown", ts=time.time() - 1))
    assert r.json() == {"state": "off"}


def test_shutdown_needs_pc_ssh(make_cfg):
    from tvwake.mock import MockPC as _Mock

    cfg = make_cfg()  # not mock mode, no PC_SSH
    clock = Clock()
    c = TestClient(create_app(cfg, WakeManager(cfg, _Mock(clock, 0, 0, initially_on=True), clock)))
    r = c.post("/shutdown", headers=headers("POST", "/shutdown"))
    assert r.status_code == 502
    assert "PC_SSH" in r.json()["detail"]["error"]
