"""Wake flow state machine.

The TV app owns the first stage (is the helper reachable?) and the last one (launch the
streaming app). The agent runs the stages in between:

    pc_up   is the PC already on and the streaming server answering?
    wake    send Wake-on-LAN
    boot    quiet period, then poll until the PC answers (ping or streaming port)
    stream  poll the streaming server port until it opens

Only one job runs at a time; a /wake call while a job is running joins it.
All waiting goes through the injected Clock, so tests run instantly with FakeClock.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Callable

from .clock import Clock
from .config import Config
from .probes import Probes

log = logging.getLogger(__name__)

PENDING, RUNNING, OK, FAILED, SKIPPED = "pending", "running", "ok", "failed", "skipped"
JOB_RUNNING, JOB_READY, JOB_FAILED = "running", "ready", "failed"
MAX_JOBS_KEPT = 10
WOL_SPACING_S = 0.5


@dataclass
class Stage:
    id: str
    title: str
    state: str = PENDING
    message: str = ""
    started_at: float | None = None
    finished_at: float | None = None

    def to_dict(self, now: float) -> dict:
        elapsed = None
        if self.started_at is not None:
            elapsed = round((self.finished_at or now) - self.started_at, 1)
        return {
            "id": self.id,
            "title": self.title,
            "state": self.state,
            "message": self.message,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "elapsed_s": elapsed,
        }


@dataclass
class Job:
    id: str
    created_at: float
    stages: list[Stage]
    state: str = JOB_RUNNING
    error: str = ""
    finished_at: float | None = None
    task: asyncio.Task | None = field(default=None, repr=False)

    def stage(self, stage_id: str) -> Stage:
        return next(s for s in self.stages if s.id == stage_id)

    def to_dict(self, now: float) -> dict:
        return {
            "job_id": self.id,
            "state": self.state,
            "error": self.error,
            "created_at": self.created_at,
            "finished_at": self.finished_at,
            "server_time": now,
            "stages": [s.to_dict(now) for s in self.stages],
        }


class WakeManager:
    def __init__(
        self, cfg: Config, probes: Probes, clock: Clock, notify: Callable[[str], object] | None = None
    ) -> None:
        self.cfg = cfg
        self.probes = probes
        self.clock = clock
        self.notify = notify  # blocking; called in a worker thread
        self._jobs: OrderedDict[str, Job] = OrderedDict()
        self._current: Job | None = None

    # ---- public API -------------------------------------------------------

    def start_or_join(self) -> Job:
        """Return the running job, or start a new one. Must be called from the event loop."""
        if self._current is not None and self._current.state == JOB_RUNNING:
            return self._current
        job = self._new_job()
        self._current = job
        job.task = asyncio.get_running_loop().create_task(self.run(job), name=f"wake-{job.id}")
        return job

    def get(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    def now(self) -> float:
        return self.clock.wall()

    async def run(self, job: Job) -> None:
        """Run a job to completion. Never raises (except cancellation): errors become a failed job."""
        log.info("wake job %s started", job.id)
        try:
            await self._flow(job)
        except asyncio.CancelledError:
            self._fail(job, "Cancelled (helper is shutting down).")
            raise
        except Exception as e:  # any bug must end up as a readable failure, not a stuck job
            log.exception("wake job %s crashed", job.id)
            self._fail(job, f"Internal error in the helper: {e.__class__.__name__}: {e}")
        log.info("wake job %s finished: %s %s", job.id, job.state, job.error)
        await self._report(job)

    async def _report(self, job: Job) -> None:
        """Tell the user (Telegram) when the PC had to be woken, or when the flow failed."""
        if self.notify is None:
            return
        took = (job.finished_at or self.now()) - job.created_at
        if job.state == JOB_FAILED:
            text = f"❌ Не удалось подготовить ПК ({took:.0f} с): {job.error}"
        elif job.stage("wake").state == OK:
            text = f"💡 ПК разбудили с ТВ, готов за {took:.0f} с"
        else:
            return  # the PC was already on: nothing worth a message
        try:
            await asyncio.to_thread(self.notify, text)
        except Exception:  # a notification must never break the job
            log.exception("notification failed")

    # ---- the flow ---------------------------------------------------------

    def _new_job(self) -> Job:
        title = self.cfg.stream_app_title
        stages = [
            Stage("pc_up", "Is the PC already on?"),
            Stage("wake", "Wake the PC"),
            Stage("boot", "Wait for the PC to start"),
            Stage("stream", f"Wait for the {title} server"),
        ]
        job = Job(id=uuid.uuid4().hex[:12], created_at=self.now(), stages=stages)
        self._jobs[job.id] = job
        while len(self._jobs) > MAX_JOBS_KEPT:
            self._jobs.popitem(last=False)
        return job

    async def _flow(self, job: Job) -> None:
        cfg = self.cfg
        pc_up = job.stage("pc_up")
        self._begin(pc_up, "Checking…")
        alive, stream_open = await asyncio.gather(
            self.probes.ping(cfg.pc_ip), self.probes.tcp_open(cfg.pc_ip, cfg.stream_port)
        )
        if stream_open:
            self._ok(pc_up, f"Yes, and the {cfg.stream_app_title} server is running.")
            for sid in ("wake", "boot", "stream"):
                self._skip(job.stage(sid), "Not needed.")
            return self._ready(job)
        if alive:
            self._ok(pc_up, "Yes, the PC is on.")
            self._skip(job.stage("wake"), "PC is already on.")
            self._skip(job.stage("boot"), "PC is already on.")
        else:
            self._ok(pc_up, "No, it is off or asleep.")
            if not await self._wake(job.stage("wake")):
                return self._fail(job)
            if not await self._wait_boot(job.stage("boot")):
                return self._fail(job)
        if not await self._wait_stream(job.stage("stream")):
            return self._fail(job)
        self._ready(job)

    async def _send_wol_burst(self) -> None:
        for i in range(self.cfg.wol_repeat):
            if i:
                await self.clock.sleep(WOL_SPACING_S)
            await self.probes.send_wol()

    async def _wake(self, st: Stage) -> bool:
        self._begin(st, "Sending Wake-on-LAN…")
        try:
            await self._send_wol_burst()
        except OSError as e:
            self._failed(st, f"Could not send the wake packet: {e}. Is WOL_BROADCAST ({self.cfg.wol_broadcast}) right?")
            return False
        self._ok(st, f"Wake packet sent to {self.cfg.pc_mac}.")
        return True

    async def _pc_answers(self) -> bool:
        alive, stream_open = await asyncio.gather(
            self.probes.ping(self.cfg.pc_ip), self.probes.tcp_open(self.cfg.pc_ip, self.cfg.stream_port)
        )
        return alive or stream_open

    async def _wait_boot(self, st: Stage) -> bool:
        cfg = self.cfg
        self._begin(st, "Giving the PC time to start…")
        start = self.clock.monotonic()
        next_resend = start + cfg.wol_resend_s if cfg.wol_resend_s > 0 else None
        while True:
            elapsed = self.clock.monotonic() - start
            if elapsed >= cfg.quiet_period_s:
                st.message = "Waiting for the PC to answer…"
                if await self._pc_answers():
                    self._ok(st, "The PC is up.")
                    return True
            elapsed = self.clock.monotonic() - start
            if elapsed >= cfg.boot_timeout_s:
                self._failed(
                    st,
                    f"The PC did not answer within {cfg.boot_timeout_s:.0f} s. "
                    "Check: Wake-on-LAN on and ErP off in the BIOS; 'Wake on Magic Packet' on in the "
                    "network adapter; Fast Startup off; the PC is wired to the same network. "
                    "If the PC did turn on, allow ping in the Windows firewall.",
                )
                return False
            if next_resend is not None and self.clock.monotonic() >= next_resend:
                try:  # packets can get lost; re-sending is harmless
                    await self.probes.send_wol()
                except OSError as e:
                    log.warning("WoL resend failed: %s", e)
                next_resend += cfg.wol_resend_s
            if elapsed < cfg.quiet_period_s:
                wait = min(cfg.poll_interval_s, cfg.quiet_period_s - elapsed)
            else:
                wait = cfg.poll_interval_s
            await self.clock.sleep(min(wait, max(0.0, cfg.boot_timeout_s - elapsed)) or 0.01)

    async def _wait_stream(self, st: Stage) -> bool:
        cfg = self.cfg
        self._begin(st, f"Waiting for port {cfg.stream_port}…")
        start = self.clock.monotonic()
        while True:
            if await self.probes.tcp_open(cfg.pc_ip, cfg.stream_port):
                self._ok(st, f"The {cfg.stream_app_title} server is ready.")
                return True
            elapsed = self.clock.monotonic() - start
            if elapsed >= cfg.stream_timeout_s:
                self._failed(st, self._stream_hint())
                return False
            await self.clock.sleep(min(cfg.poll_interval_s, cfg.stream_timeout_s - elapsed))

    def _stream_hint(self) -> str:
        cfg = self.cfg
        base = (
            f"The PC is on, but nothing answered on port {cfg.stream_port} "
            f"within {cfg.stream_timeout_s:.0f} s. Likely causes: "
        )
        if cfg.stream_backend == "steamlink":
            return base + (
                "Windows does not log in automatically; Steam is not in autostart; "
                "Remote Play is disabled in Steam; the Windows firewall blocks Steam."
            )
        return base + (
            "Sunshine/Apollo is not installed or its service is stopped; "
            "the Windows firewall blocks it; Windows is still starting up (try Retry)."
        )

    # ---- state helpers ----------------------------------------------------

    def _begin(self, st: Stage, message: str) -> None:
        st.state, st.message, st.started_at = RUNNING, message, self.now()
        log.info("stage %s: %s", st.id, message)

    def _ok(self, st: Stage, message: str) -> None:
        st.state, st.message, st.finished_at = OK, message, self.now()
        log.info("stage %s ok: %s", st.id, message)

    def _skip(self, st: Stage, message: str) -> None:
        st.state, st.message = SKIPPED, message

    def _failed(self, st: Stage, message: str) -> None:
        st.state, st.message, st.finished_at = FAILED, message, self.now()
        log.warning("stage %s failed: %s", st.id, message)

    def _ready(self, job: Job) -> None:
        job.state, job.finished_at = JOB_READY, self.now()

    def _fail(self, job: Job, error: str | None = None) -> None:
        """Mark the job failed. Without `error`, use the failed stage's message."""
        if error is None:
            failed = next((s for s in job.stages if s.state == FAILED), None)
            error = failed.message if failed else "Unknown error."
        else:
            for st in job.stages:
                if st.state == RUNNING:
                    self._failed(st, error)
        for st in job.stages:
            if st.state == PENDING:
                st.state = SKIPPED
        job.state, job.error, job.finished_at = JOB_FAILED, error, self.now()
