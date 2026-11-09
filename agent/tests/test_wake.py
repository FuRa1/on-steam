import asyncio

import pytest

from tvwake.clock import FakeClock
from tvwake.mock import MockPC
from tvwake.wake import WakeManager


class FakePC:
    """Scripted PC: answers ping from `ping_at`, opens the port from `stream_at` (virtual seconds)."""

    def __init__(self, clock, ping_at=None, stream_at=None, wol_error=None, crash=False):
        self.clock = clock
        self.ping_at = ping_at
        self.stream_at = stream_at
        self.wol_error = wol_error
        self.crash = crash
        self.wol_times = []
        self.ping_times = []

    async def ping(self, ip):
        if self.crash:
            raise RuntimeError("boom")
        self.ping_times.append(self.clock.monotonic())
        return self.ping_at is not None and self.clock.monotonic() >= self.ping_at

    async def tcp_open(self, ip, port):
        return self.stream_at is not None and self.clock.monotonic() >= self.stream_at

    async def send_wol(self):
        if self.wol_error:
            raise self.wol_error
        self.wol_times.append(self.clock.monotonic())


def run_job(cfg, pc, clock):
    async def go():
        mgr = WakeManager(cfg, pc, clock)
        job = mgr.start_or_join()
        await job.task
        return job

    return asyncio.run(go())


def states(job):
    return {s.id: s.state for s in job.stages}


def test_pc_already_streaming_skips_everything(make_cfg):
    clock = FakeClock()
    pc = FakePC(clock, ping_at=0, stream_at=0)
    job = run_job(make_cfg(), pc, clock)
    assert job.state == "ready"
    assert states(job) == {"pc_up": "ok", "wake": "skipped", "boot": "skipped", "stream": "skipped"}
    assert pc.wol_times == []


def test_pc_on_but_server_not_yet(make_cfg):
    clock = FakeClock()
    pc = FakePC(clock, ping_at=0, stream_at=10)
    job = run_job(make_cfg(), pc, clock)
    assert job.state == "ready"
    assert states(job) == {"pc_up": "ok", "wake": "skipped", "boot": "skipped", "stream": "ok"}
    assert pc.wol_times == []
    assert 10 <= clock.monotonic() <= 14


def test_full_wake_flow_with_quiet_period(make_cfg):
    clock = FakeClock()
    pc = FakePC(clock, ping_at=45, stream_at=70)
    job = run_job(make_cfg(), pc, clock)
    assert job.state == "ready", job.error
    assert states(job) == {"pc_up": "ok", "wake": "ok", "boot": "ok", "stream": "ok"}
    # 3 packets in the initial burst, 0.5 s apart
    assert pc.wol_times[:3] == [0, 0.5, 1.0]
    # no pings during the 30 s quiet period (after the initial "already up?" check)
    boot_start = 1.0
    assert not [t for t in pc.ping_times[1:] if t < boot_start + 30]
    # elapsed time is reported per stage
    boot = job.stage("boot").to_dict(clock.wall())
    assert 44 <= boot["elapsed_s"] <= 50


def test_wol_resent_while_waiting(make_cfg):
    clock = FakeClock()
    pc = FakePC(clock, ping_at=100, stream_at=100)
    run_job(make_cfg(), pc, clock)
    assert len(pc.wol_times) >= 3 + 4  # burst + resends every 20 s


def test_boot_timeout(make_cfg):
    clock = FakeClock()
    pc = FakePC(clock)  # never comes up
    job = run_job(make_cfg(), pc, clock)
    assert job.state == "failed"
    assert states(job) == {"pc_up": "ok", "wake": "ok", "boot": "failed", "stream": "skipped"}
    assert "BIOS" in job.error
    elapsed = job.stage("boot").finished_at - job.stage("boot").started_at
    assert 150 <= elapsed <= 155


def test_stream_timeout_hint_moonlight(make_cfg):
    clock = FakeClock()
    pc = FakePC(clock, ping_at=40)  # boots, server never starts
    job = run_job(make_cfg(), pc, clock)
    assert job.state == "failed"
    assert job.stage("stream").state == "failed"
    assert "Sunshine/Apollo" in job.error


def test_stream_timeout_hint_steamlink(make_cfg):
    clock = FakeClock()
    pc = FakePC(clock, ping_at=0)
    job = run_job(make_cfg(STREAM_BACKEND="steamlink"), pc, clock)
    assert "auto" in job.error and "Steam" in job.error


def test_wol_send_error(make_cfg):
    clock = FakeClock()
    pc = FakePC(clock, wol_error=OSError("Network is unreachable"))
    job = run_job(make_cfg(), pc, clock)
    assert job.state == "failed"
    assert job.stage("wake").state == "failed"
    assert "unreachable" in job.error


def test_crash_becomes_failed_job(make_cfg):
    clock = FakeClock()
    pc = FakePC(clock, crash=True)
    job = run_job(make_cfg(), pc, clock)
    assert job.state == "failed"
    assert "Internal error" in job.error
    assert job.stage("pc_up").state == "failed"
    assert all(s.state != "running" for s in job.stages)


def test_concurrent_wake_joins_running_job(make_cfg):
    async def go():
        clock = FakeClock()
        pc = FakePC(clock, ping_at=40, stream_at=50)
        mgr = WakeManager(make_cfg(), pc, clock)
        a = mgr.start_or_join()
        b = mgr.start_or_join()
        assert a is b
        await a.task
        c = mgr.start_or_join()  # finished job -> a new one starts
        assert c is not a
        await c.task
        assert mgr.get(a.id) is a and mgr.get(c.id) is c

    asyncio.run(go())


@pytest.mark.parametrize("fail,expect", [("", "ready"), ("boot", "failed"), ("stream", "failed")])
def test_mock_pc(make_cfg, fail, expect):
    clock = FakeClock()
    cfg = make_cfg(MOCK_PC=1)
    pc = MockPC(clock, boot_s=20, stream_s=10, fail=fail)
    job = run_job(cfg, pc, clock)
    assert job.state == expect
    failed = [s.id for s in job.stages if s.state == "failed"]
    assert failed == ({"": [], "boot": ["boot"], "stream": ["stream"]}[fail])


def test_mock_pc_auto_off(make_cfg):
    clock = FakeClock()
    pc = MockPC(clock, boot_s=0, stream_s=0, initially_on=True, auto_off_s=60)
    assert asyncio.run(pc.ping("x"))
    clock.advance(61)
    assert not asyncio.run(pc.ping("x"))


def run_job_notify(cfg, pc, clock):
    sent = []

    async def go():
        mgr = WakeManager(cfg, pc, clock, notify=sent.append)
        job = mgr.start_or_join()
        await job.task
        return job

    return asyncio.run(go()), sent


def test_notifies_only_when_the_pc_was_woken(make_cfg):
    clock = FakeClock()
    _, sent = run_job_notify(make_cfg(), FakePC(clock, ping_at=0, stream_at=0), clock)
    assert sent == []  # already on: no message
    clock = FakeClock()
    _, sent = run_job_notify(make_cfg(QUIET_PERIOD_S=0), FakePC(clock, ping_at=6, stream_at=8), clock)
    assert len(sent) == 1 and sent[0].startswith("💡") and "ready in" in sent[0]


def test_notifies_failures(make_cfg):
    clock = FakeClock()
    job, sent = run_job_notify(make_cfg(), FakePC(clock), clock)
    assert job.state == "failed"
    assert len(sent) == 1 and sent[0].startswith("❌")
