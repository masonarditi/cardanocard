import asyncio
import hmac
import os
from pathlib import Path
from contextlib import asynccontextmanager, suppress
from typing import Literal

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict

from .engine import Conflict, Engine
from .models import INPUT_SCHEMA, ProvideInput, StartRequest
from .providers import FakeEscrow, FakePurchaser
from .store import Store
from .event_output import event_line, feed_banner
from .agentcard_bridge import AgentCardPurchaser, ReplayTransport


class LocalStart(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request: StartRequest
    scenario: str = "success"


def configured_engine():
    mode = os.getenv("CARDANO_CARD_MODE", "local")
    if mode not in {"local", "preprod"}:
        raise ValueError("Only local and Preprod are supported")
    # Real merchant charging is deliberately gated until Preprod/contract acceptance tests pass.
    backend = os.getenv("PURCHASE_BACKEND", "fake")
    if backend not in {"fake", "replay"}:
        raise ValueError("Live purchasing is not enabled in this scaffold; validate Mason's contract first")
    store = Store(os.getenv("CARDANO_CARD_DB", "data/jobs.db"))
    try:
        if mode == "preprod":
            if os.getenv("MASUMI_V1_COMPATIBLE") != "true":
                raise ValueError("Validate the selected Payment Service against the pinned V1 SDK before Preprod use")
            from .masumi_adapter import MasumiEscrow
            escrow = MasumiEscrow(os.getenv("PAYMENT_SERVICE_URL", ""), os.getenv("PAYMENT_API_KEY", ""),
                                 os.getenv("AGENT_IDENTIFIER", ""), os.getenv("SELLER_VKEY", ""))
        else:
            escrow = FakeEscrow(store)
        purchaser = (AgentCardPurchaser(store, ReplayTransport(store, os.getenv("REPLAY_SCENARIO", "success")))
                     if backend == "replay" else FakePurchaser(store, os.getenv("FAKE_SCENARIO", "success")))
        return Engine(store, escrow, purchaser)
    except Exception:
        store.close()
        raise


def create_app(engine=None, token=None, background=True, poll_seconds=2, frontend=True, live_output=False):
    token = token or os.getenv("CARDANO_CARD_TOKEN", "")
    if len(token) < 24:
        raise ValueError("Set CARDANO_CARD_TOKEN in .env to a random value of at least 24 characters")

    async def authorized(authorization: str | None = Header(default=None)):
        if not authorization or not hmac.compare_digest(authorization, "Bearer " + token):
            raise HTTPException(401, "A valid demo caller token is required")

    @asynccontextmanager
    async def lifespan(app):
        owned = engine is None
        app.state.engine = engine or configured_engine()
        previous_sink = app.state.engine.store.event_sink
        if live_output:
            ascii_only = os.getenv("CARDANO_CARD_ASCII", "false").lower() == "true"
            app.state.engine.store.event_sink = lambda event: print(event_line(event, ascii_only=ascii_only), flush=True)
            print(feed_banner() + "\nWaiting for saved job events...", flush=True)

        async def worker():
            while True:
                await app.state.engine.tick()
                await asyncio.sleep(max(0.1, poll_seconds))

        task = asyncio.create_task(worker()) if background else None
        try:
            yield
        finally:
            if task:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
            if owned:
                app.state.engine.store.close()
            else:
                app.state.engine.store.event_sink = previous_sink

    app = FastAPI(title="Cardano Card — local integration scaffold", lifespan=lifespan)
    static = Path(__file__).with_name("static")
    if frontend:
        app.mount("/assets", StaticFiles(directory=static), name="assets")

    @app.middleware("http")
    async def local_headers(request, call_next):
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Content-Type-Options"] = "nosniff"
        if request.url.path == "/" or request.url.path.startswith("/assets/"):
            response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        return response

    @app.get("/", include_in_schema=False)
    async def demo():
        if frontend:
            return FileResponse(static / "index.html")
        return {"service": "cardano-card", "visibility": "terminal",
                "command": "python -m cardano_card.terminal status"}

    def service():
        return app.state.engine

    @app.get("/session", dependencies=[Depends(authorized)])
    async def session():
        return {"authenticated": True}

    @app.get("/jobs", dependencies=[Depends(authorized)])
    async def jobs(limit: int = Query(default=20, ge=1, le=100)):
        return {"jobs": [{key: job[key] for key in ("id", "phase", "escrow_state", "simulated_escrow", "simulated_purchase")}
                         for job in service().store.jobs()[-limit:][::-1]]}

    @app.get("/events", dependencies=[Depends(authorized)])
    async def events(after: int | None = Query(default=None, ge=0), limit: int = Query(default=20, ge=1, le=100)):
        try:
            return service().store.event_feed(after, limit)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.exception_handler(Conflict)
    async def conflict(_, exc):
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @app.exception_handler(KeyError)
    async def missing(_, exc):
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=404, content={"detail": "Job not found"})

    @app.get("/availability")
    async def availability():
        return {"status": "available", "type": "masumi-agent",
                "simulated_escrow": service().escrow.simulated,
                "simulated_purchase": service().purchaser.simulated,
                "purchase_backend": "replay" if isinstance(service().purchaser, AgentCardPurchaser) and service().purchaser.simulated else "fake" if isinstance(service().purchaser, FakePurchaser) else "external",
                "message": "Local scaffold; registry and on-chain acceptance not yet verified"}

    @app.get("/input_schema")
    async def schema():
        return INPUT_SCHEMA

    @app.post("/start_job", dependencies=[Depends(authorized)])
    async def start(request: StartRequest):
        job = await service().start(request)
        response = {"id": job["id"], "identifierFromPurchaser": job["caller_id"],
                    "phase": job["phase"], "simulated_escrow": job["simulated_escrow"],
                    "simulated_purchase": job["simulated_purchase"]}
        if job["payment"]:
            response.update(job["payment"])
        else:
            # Return the reserved job ID so a caller can reconcile instead of paying twice.
            from fastapi.responses import JSONResponse
            return JSONResponse(status_code=202, content=response)
        return response

    @app.post("/local/start_job", dependencies=[Depends(authorized)])
    async def local_start(body: LocalStart):
        if not service().escrow.simulated or not service().purchaser.simulated:
            raise HTTPException(404, "Local demo setup requires simulated providers")
        scenarios = ReplayTransport.SCENARIOS if isinstance(service().purchaser, AgentCardPurchaser) else FakePurchaser.SCENARIOS
        if body.scenario not in scenarios:
            raise HTTPException(422, "Unsupported demo scenario")
        # Unfunded jobs cannot start purchasing while the demo scenario is being saved.
        job = await service().start(body.request)
        prior = service().store.get("demo_scenarios", job["id"])
        if prior and prior["scenario"] != body.scenario:
            raise Conflict("Existing request uses a different demo scenario")
        if not prior and job["purchase_started"]:
            raise Conflict("Cannot change a purchase that has started")
        service().store.put("demo_scenarios", job["id"], {"scenario": body.scenario})
        return {"id": job["id"], "phase": job["phase"]}

    @app.get("/evidence", dependencies=[Depends(authorized)])
    async def evidence(job_id: str = Query()):
        return service().evidence(service().get(job_id))

    @app.get("/status", dependencies=[Depends(authorized)])
    async def status(job_id: str = Query()):
        return service().public(service().get(job_id))

    @app.post("/provide_input", dependencies=[Depends(authorized)])
    async def provide(request: ProvideInput):
        await service().provide(request)
        return {"accepted": True}

    @app.post("/local/jobs/{job_id}/{action}", dependencies=[Depends(authorized)])
    async def simulate(job_id: str, action: Literal["fund", "request_refund", "withdraw"]):
        if not service().escrow.simulated:
            raise HTTPException(404, "Local simulation is disabled")
        try:
            await service().simulate(job_id, action)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        return {"simulated": True, "action": action}

    return app


def main():
    load_dotenv()
    import uvicorn
    app = create_app(poll_seconds=float(os.getenv("CARDANO_CARD_POLL_SECONDS", "2")),
                     frontend=os.getenv("CARDANO_CARD_FRONTEND", "false").lower() == "true",
                     live_output=os.getenv("CARDANO_CARD_LIVE_OUTPUT", "true").lower() == "true")
    # Single process, loopback only. Exposure/hosting is a later explicit step.
    uvicorn.run(app, host="127.0.0.1", port=int(os.getenv("CARDANO_CARD_PORT", "8080")), access_log=False)
