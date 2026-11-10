"""HTTP API used by the TV app.

GET  /health          unauthenticated: {ok, version, server_time, mock}
POST /wake            start or join the wake job: {job_id}
GET  /jobs/{job_id}   job state
GET  /devmode         dev mode session info from the renewal state file
GET  /pc              {up}: does the PC answer ping (the "PC Off" app waits for false)
POST /shutdown        shut the PC down now: {state: "sent" | "off"} ("off" = it was off already);
                      after "sent" the agent waits for the PC to go quiet and shows a toast on the TV

The TV app runs from a file:// origin (Origin: null) and sends custom headers, so the
browser does a CORS preflight. We allow any origin: authentication is the HMAC signature,
not the origin.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from . import __version__, devmode, power
from .auth import AuthError, Verifier
from .config import Config
from .wake import WakeManager

log = logging.getLogger(__name__)


def create_app(cfg: Config, manager: WakeManager,
               tv_toast: Callable[[str], Awaitable[object]] | None = None) -> FastAPI:
    app = FastAPI(title="tv-wake-launcher agent", version=__version__, docs_url=None, redoc_url=None)
    background: set[asyncio.Task] = set()  # keep references, or the tasks may be collected
    verifier = Verifier(cfg.shared_secret, wall=manager.clock.wall)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["X-Timestamp", "X-Signature", "X-Client", "Content-Type"],
        max_age=600,
    )

    async def require_auth(request: Request) -> None:
        body = await request.body()
        try:
            verifier.verify(
                request.method,
                request.url.path,
                request.headers.get("x-timestamp"),
                request.headers.get("x-signature"),
                body,
            )
        except AuthError as e:
            log.warning("rejected %s %s from %s: %s", request.method, request.url.path,
                        request.client.host if request.client else "?", e.reason)
            # server_time lets the TV correct for a wrong clock and retry.
            raise HTTPException(401, detail={"error": e.reason, "server_time": manager.clock.wall()}) from None

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled error on %s %s", request.method, request.url.path)
        return JSONResponse({"detail": {"error": f"internal error: {exc.__class__.__name__}"}}, status_code=500)

    @app.get("/health")
    async def health() -> dict:
        return {"ok": True, "version": __version__, "server_time": manager.clock.wall(), "mock": cfg.mock_pc}

    @app.post("/wake", dependencies=[Depends(require_auth)])
    async def wake(request: Request) -> dict:
        job = manager.start_or_join()
        log.info("wake requested by %s (%s) -> job %s", request.client.host if request.client else "?",
                 request.headers.get("x-client", "unknown client"), job.id)
        return {"job_id": job.id}

    @app.get("/jobs/{job_id}", dependencies=[Depends(require_auth)])
    async def job(job_id: str) -> dict:
        j = manager.get(job_id)
        if j is None:
            # e.g. the helper restarted; the TV app then starts a new job.
            raise HTTPException(404, detail={"error": "unknown job"})
        return j.to_dict(manager.now())

    @app.get("/pc", dependencies=[Depends(require_auth)])
    async def pc() -> dict:
        return {"up": await power.pc_is_up(cfg, manager.probes)}

    @app.post("/shutdown", dependencies=[Depends(require_auth)])
    async def shutdown(request: Request) -> dict:
        log.info("shutdown requested by %s (%s)", request.client.host if request.client else "?",
                 request.headers.get("x-client", "unknown client"))
        try:
            state = await power.shutdown(cfg, manager.probes)
        except power.PowerError as e:
            raise HTTPException(502, detail={"error": str(e)}) from None
        if state == "sent" and tv_toast is not None:
            task = asyncio.create_task(power.report_when_off(cfg, manager.probes, tv_toast))
            background.add(task)
            task.add_done_callback(background.discard)
        return {"state": state}

    @app.get("/devmode", dependencies=[Depends(require_auth)])
    async def devmode_info() -> dict:
        return devmode.api_view(cfg)

    return app
