import asyncio
import hashlib
import time
import json
import hmac
import os
import sys
from pathlib import Path
from contextlib import asynccontextmanager, suppress
from typing import Literal

from dotenv import load_dotenv
from fastapi.responses import PlainTextResponse
from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict

from .engine import Conflict, Engine
from .hosted_escrow import HostedMasumiEscrow
from .masumi_adapter import MasumiEscrow
from .models import INPUT_SCHEMA, ProvideInput, StartRequest
from .providers import FakeEscrow, FakePurchaser, ModulePurchaser
from .store import Store
from .event_output import event_line, feed_banner
from .agentcard_bridge import AgentCardPurchaser, ReplayTransport
from .staged_engine import StagedEngine
from .staged_purchase import FakeStagedModule, StagedModule


class LocalStart(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request: StartRequest
    scenario: str = "success"


def agentcard_dir():
    """Mason's module directory: AGENTCARD_DIR (hosted image) or ../../agentcard in a source checkout."""
    return os.getenv("AGENTCARD_DIR") or str(Path(__file__).resolve().parents[3] / "agentcard")


def configured_engine():
    mode = os.getenv("CARDANO_CARD_MODE", "local")
    # hosted = Cardano Preprod escrow through Masumi's hosted payment service (no local node).
    if mode not in {"local", "preprod", "hosted"}:
        raise ValueError("Only local, Preprod and hosted (Preprod) modes are supported")
    # mason = real AgentCard through ../agentcard/purchase.py (sandbox unless AGENTCARD_ENV=prod).
    backend = os.getenv("PURCHASE_BACKEND", "fake")
    if backend not in {"fake", "replay", "mason", "staged_fake", "staged_module"}:
        raise ValueError("PURCHASE_BACKEND must be fake, replay, mason, staged_fake or staged_module")
    if backend == "staged_module" and (mode != "preprod" or
            os.getenv("ALLOW_EXTERNAL_VAULT_CHECKOUT") != "true" or
            os.getenv("VAULT_OPERATOR_EXCLUSIVE") != "true"):
        raise ValueError("External Vault checkout requires Preprod escrow and explicit execution/operator flags")
    if backend == "mason" and mode == "local" and os.getenv("AGENTCARD_ENV") == "prod":
        # Local escrow is fake: anyone with the API token could "fund" a job and spend the real card.
        raise ValueError("Production AgentCard purchases require Preprod escrow (CARDANO_CARD_MODE=preprod)")
    store = Store(os.getenv("CARDANO_CARD_DB", "data/jobs.db"))
    try:
        if mode == "hosted":
            from .hosted_escrow import HostedMasumiEscrow
            escrow = HostedMasumiEscrow(os.getenv("PAYMENT_SERVICE_URL", ""), os.getenv("PAYMENT_API_KEY", ""),
                                        os.getenv("AGENT_IDENTIFIER", ""), os.getenv("SELLER_VKEY", ""),
                                        os.getenv("PAYOUT_ADDRESS", ""), os.getenv("MASUMI_FEE_LOVELACE", "10000000"),
                                        os.getenv("MASUMI_PAYMENT_SOURCE_INDEX", "0"),
                                        lovelace_per_usd=os.getenv("MASUMI_LOVELACE_PER_USD") or None,
                                        deadlines_min=[x for x in os.getenv("MASUMI_DEADLINES_MIN", "").split(",") if x.strip()] or None,
                                        source_type=os.getenv("MASUMI_PAYMENT_SOURCE_TYPE", "Web3CardanoV2"),
                                        auth_header=os.getenv("MASUMI_AUTH_HEADER", "x-api-key"))
        elif mode == "preprod":
            if os.getenv("MASUMI_V1_COMPATIBLE") != "true":
                raise ValueError("Validate the selected Payment Service against the pinned V1 SDK before Preprod use")
            from .masumi_adapter import MasumiEscrow
            escrow = MasumiEscrow(os.getenv("PAYMENT_SERVICE_URL", ""), os.getenv("PAYMENT_API_KEY", ""),
                                 os.getenv("AGENT_IDENTIFIER", ""), os.getenv("SELLER_VKEY", ""),
                                 payout_address=os.getenv("PAYOUT_ADDRESS") or None,
                                 allow_remote=os.getenv("MASUMI_ALLOW_REMOTE_NODE") == "true")
        else:
            escrow = FakeEscrow(store)
        if backend in {"staged_fake", "staged_module"}:
            if backend == "staged_module":
                sys.path.insert(0, agentcard_dir())
            module = (FakeStagedModule(store, os.getenv("FAKE_SCENARIO", "success")) if backend == "staged_fake"
                      else StagedModule(os.environ["MASON_STAGED_MODULE"]))
            return StagedEngine(store, escrow, module,
                escrow_lovelace=int(os.getenv("MASUMI_FEE_LOVELACE", "10000000")),
                payout_address=os.getenv("PAYOUT_ADDRESS", "SIM-payout" if mode == "local" else ""))
        if backend == "mason":
            sys.path.insert(0, agentcard_dir())
            purchaser = ModulePurchaser("purchase")
        else:
            purchaser = (AgentCardPurchaser(store, ReplayTransport(store, os.getenv("REPLAY_SCENARIO", "success")))
                         if backend == "replay" else FakePurchaser(store, os.getenv("FAKE_SCENARIO", "success")))
        return Engine(store, escrow, purchaser)
    except Exception:
        store.close()
        raise


def create_app(engine=None, token=None, background=True, poll_seconds=2, frontend=True, live_output=False,
               public_jobs=None):
    token = token or os.getenv("CARDANO_CARD_TOKEN", "")
    if len(token) < 24:
        raise ValueError("Set CARDANO_CARD_TOKEN in .env to a random value of at least 24 characters")
    # MIP-003 buyers discover the agent in the registry and carry no token of ours: when public_jobs is on, the
    # job endpoints are open (escrow funding is the gate); operator routes stay behind the bearer token.
    if public_jobs is None:
        public_jobs = os.getenv("CARDANO_CARD_PUBLIC_JOBS", "false").lower() == "true"

    async def authorized(authorization: str | None = Header(default=None)):
        if not authorization or not hmac.compare_digest(authorization, "Bearer " + token):
            raise HTTPException(401, "A valid demo caller token is required")

    async def job_caller(authorization: str | None = Header(default=None)):
        if not public_jobs:
            await authorized(authorization)

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
                "purchase_backend": ("staged_fake" if service().purchaser.simulated else "staged_module") if isinstance(service(), StagedEngine) else "replay" if isinstance(service().purchaser, AgentCardPurchaser) and service().purchaser.simulated else "fake" if isinstance(service().purchaser, FakePurchaser) else "external",
                "network": "Preprod" if isinstance(service().escrow, (MasumiEscrow, HostedMasumiEscrow)) else "simulated",
                "escrow_rail": ("hosted-v2" if isinstance(service().escrow, HostedMasumiEscrow) else
                                "self-hosted-v1" if isinstance(service().escrow, MasumiEscrow) else "simulated"),
                "message": (("Cardano Card on Preprod: fund the escrow returned by /start_job, then poll /status. " +
                             ("Merchant purchasing is simulated (no card)." if service().purchaser.simulated else
                              "Purchases go through Agentcard on a real card; a failed purchase refunds your escrow."
                              if os.getenv("AGENTCARD_ENV") == "prod" else
                              "Purchases go through the Agentcard sandbox: a real cart is built, the checkout is declined "
                              "(sandbox_mode) and your escrow is refunded."))
                            if not service().purchaser.simulated or isinstance(service().escrow, HostedMasumiEscrow)
                            else "Local scaffold; registry and on-chain acceptance not yet verified")}

    @app.get("/input_schema")
    async def schema():
        return INPUT_SCHEMA

    @app.get("/get-credential", response_class=PlainTextResponse)
    async def masumi_verification(masumi_challenge: str = Query(min_length=1, max_length=200)):
        # Masumi SaaS ownership check: GET {apiUrl}/get-credential?masumi_challenge=... must return
        # HMAC-SHA256(challenge, MASUMI_VERIFICATION_SECRET) as hex plain text (masumi-saas agent-verification.ts).
        secret = os.getenv("MASUMI_VERIFICATION_SECRET", "")
        if not secret:
            raise HTTPException(404, "Verification is not configured")
        return hmac.new(secret.encode(), masumi_challenge.encode(), hashlib.sha256).hexdigest()

    @app.post("/start_job", dependencies=[Depends(job_caller)])
    async def start(request: StartRequest):
        job = await service().start(request)
        response = {"id": job["id"], "identifierFromPurchaser": job["caller_id"],
                    "phase": job["phase"], "simulated_escrow": job["simulated_escrow"],
                    "simulated_purchase": job["simulated_purchase"]}
        if job["payment"] and (not isinstance(service(), StagedEngine) or job["phase"] == "awaiting_payment"):
            response.update(job["payment"])
            # Sokosumi's paid-job schema (packages/masumi start_job.schema.ts) reads snake_case input_hash and,
            # for V2, the supportedPaymentSourceIndex to pick the priced source; keep our camelCase too.
            response["input_hash"] = job["payment"].get("inputHash")
            response.setdefault("supportedPaymentSourceIndex", job["payment"].get("supportedPaymentSourceIndex", 0))
        else:
            # Return the reserved job ID so a caller can reconcile instead of paying twice.
            from fastapi.responses import JSONResponse
            return JSONResponse(status_code=202, content=response)
        return response

    @app.post("/local/start_job", dependencies=[Depends(authorized)])
    async def local_start(body: LocalStart):
        if not service().escrow.simulated or not service().purchaser.simulated:
            raise HTTPException(404, "Local demo setup requires simulated providers")
        scenarios = FakeStagedModule.SCENARIOS if isinstance(service(), StagedEngine) else ReplayTransport.SCENARIOS if isinstance(service().purchaser, AgentCardPurchaser) else FakePurchaser.SCENARIOS
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

    @app.get("/diagnostics", dependencies=[Depends(authorized)])
    async def diagnostics(probe: bool = Query(default=False)):
        """Operator view: which rails are wired and (with ?probe=true) whether they answer. Read-only."""
        engine_ = service()
        out = {"escrow_rail": ("hosted-v2" if isinstance(engine_.escrow, HostedMasumiEscrow) else
                               "self-hosted-v1" if isinstance(engine_.escrow, MasumiEscrow) else "simulated"),
               "purchase_backend": "external" if not engine_.purchaser.simulated else "simulated",
               "agentcard_env": os.getenv("AGENTCARD_ENV") or "sandbox", "jobs": len(engine_.store.jobs())}
        module = getattr(engine_.purchaser, "module", None)
        if module is not None:
            ac = sys.modules.get("agentcard")
            if ac is not None:
                try:
                    tokens = json.loads(ac.TOKENS.read_text()) if ac.TOKENS.exists() else {}
                    out["agentcard"] = {"user_id": tokens.get("user_id"), "token_file": str(ac.TOKENS),
                                        "access_expires_in_s": int(tokens.get("expires_at", 0) - time.time()) if tokens else None}
                except Exception as exc:  # never leak token contents
                    out["agentcard"] = {"error": type(exc).__name__}
        if probe:
            try:
                if isinstance(engine_.escrow, MasumiEscrow):
                    out["escrow_probe"] = await engine_.escrow.validate_payout()
                ac = sys.modules.get("agentcard")
                if module is not None and ac is not None:
                    import requests
                    r = await asyncio.to_thread(lambda: requests.get(ac.BASE + "/buy/merchants", timeout=30,
                                                                     headers={"Authorization": f"Bearer {ac.user_token()}"}))
                    out["agentcard_probe"] = {"status": r.status_code, "merchants": len((r.json() or {}).get("merchants", []))}
            except Exception as exc:
                out["probe_error"] = f"{type(exc).__name__}: {str(exc)[:200]}"
        return out

    @app.get("/operator/jobs/{job_id}", dependencies=[Depends(authorized)])
    async def operator_job(job_id: str):
        """Full stored job for operator tooling (hosted_evidence verifies its settlement independently)."""
        return service().get(job_id)

    @app.get("/evidence", dependencies=[Depends(authorized)])
    async def evidence(job_id: str = Query()):
        return service().evidence(service().get(job_id))

    @app.get("/status", dependencies=[Depends(job_caller)])
    async def status(job_id: str = Query()):
        return service().public(service().get(job_id))

    @app.post("/provide_input", dependencies=[Depends(job_caller)])
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


class PathPrefixes:
    """Serve the same app under extra base paths (e.g. /v2) so several registry entries can point at one deployment
    with distinct apiBaseUrl values; the Masumi SaaS matches registrations to their NFT by exact URL."""
    def __init__(self, app, prefixes):
        self.app = app
        self.prefixes = tuple(p.rstrip("/") for p in prefixes if p.strip("/"))

    async def __call__(self, scope, receive, send):
        if scope["type"] in {"http", "websocket"}:
            path = scope.get("path", "")
            for prefix in self.prefixes:
                if path == prefix or path.startswith(prefix + "/"):
                    scope = {**scope, "path": path[len(prefix):] or "/", "raw_path": (path[len(prefix):] or "/").encode()}
                    break
        await self.app(scope, receive, send)


def main():
    load_dotenv()
    import uvicorn
    app = create_app(poll_seconds=float(os.getenv("CARDANO_CARD_POLL_SECONDS", "2")),
                     frontend=os.getenv("CARDANO_CARD_FRONTEND", "false").lower() == "true",
                     live_output=os.getenv("CARDANO_CARD_LIVE_OUTPUT", "true").lower() == "true")
    prefixes = [p for p in os.getenv("CARDANO_CARD_PATH_PREFIXES", "").split(",") if p.strip()]
    if prefixes:
        app = PathPrefixes(app, prefixes)
    # Single process. Loopback by default; the hosted deployment sets CARDANO_CARD_HOST=0.0.0.0 behind Railway's proxy.
    uvicorn.run(app, host=os.getenv("CARDANO_CARD_HOST", "127.0.0.1"),
                port=int(os.getenv("PORT") or os.getenv("CARDANO_CARD_PORT", "8080")), access_log=False)


if __name__ == "__main__":
    main()
