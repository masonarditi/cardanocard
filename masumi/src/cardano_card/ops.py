"""Operator page (`/ops`) and the card kill switch.

The page is served by the runtime itself so it is reachable wherever the agent is (Railway, a phone during the demo)
and gated by the same bearer token as the other operator routes. The kill switch lives in the job store, so flipping
it takes effect on the next purchase without a redeploy: with the card OFF, `GuardedPurchaser` answers every
purchase with a definitive `card_disabled` failure (no charge) and the engine refunds the buyer as usual. A purchase
already in flight when the switch flips is not interrupted (cancelling a thread cannot stop a card charge).
"""
import asyncio
import json
import os
import sys
import time
from pathlib import Path

import httpx
from fastapi import Depends, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

STATIC = Path(__file__).with_name("static")
SOKOSUMI_AGENTS = os.getenv("SOKOSUMI_AGENTS_URL", "https://api.preprod.sokosumi.com/v1/agents")
SOKOSUMI_NAME = os.getenv("SOKOSUMI_AGENT_NAME", "CardanoCard")
V1_NODE_URL = os.getenv("V1_NODE_URL", "https://cardanocard-node-production.up.railway.app/api/v1")
STUCK_AFTER_S = 15 * 60
OPEN_PHASES = {"purchasing", "reconciling", "processing", "submitting_result", "creating_payment", "refund_authorizing"}
ATTENTION_PHASES = {"manual_review", "payment_creation_unknown"}


def card_enabled(store):
    record = store.get("ops", "card")
    return True if record is None else bool(record.get("enabled", True))


def set_card(store, enabled, note=""):
    record = {"enabled": bool(enabled), "changed_at": time.time(), "note": str(note)[:200]}
    store.put("ops", "card", record)
    log = store.get("ops", "log") or []
    log.append({"at": record["changed_at"], "message": f"Card switched {'ON' if enabled else 'OFF'}" + (f" — {record['note']}" if record["note"] else "")})
    store.put("ops", "log", log[-50:])
    return record


class GuardedPurchaser:
    """Wraps the real purchaser; honors the kill switch before any provider call. Inspect always passes through."""

    def __init__(self, inner, store):
        self.inner, self.store = inner, store
        self.simulated = inner.simulated

    @property
    def module(self):
        return getattr(self.inner, "module", None)

    async def purchase(self, inputs, request_id, response=None):
        if not card_enabled(self.store):
            return {"status": "failed", "reason": "card_disabled"}
        return await self.inner.purchase(inputs, request_id, response)

    async def inspect(self, request_id):
        return await self.inner.inspect(request_id)


class Probes:
    """Cached, single-flight checks of the external rails so a 5-second page refresh never hammers them."""
    TTL = {"agentcard": 120, "hosted": 20, "sokosumi": 300, "node": 60}

    def __init__(self):
        self.cache, self.locks = {}, {}

    async def get(self, name, fn):
        hit = self.cache.get(name)
        if hit and time.time() - hit["at"] < self.TTL[name]:
            return hit
        async with self.locks.setdefault(name, asyncio.Lock()):
            hit = self.cache.get(name)
            if hit and time.time() - hit["at"] < self.TTL[name]:
                return hit
            try:
                record = {"ok": True, "at": time.time(), **(await asyncio.wait_for(fn(), 25))}
            except Exception as exc:
                record = {"ok": False, "at": time.time(), "error": f"{type(exc).__name__}: {str(exc)[:160]}"}
            self.cache[name] = record
            return record


async def probe_agentcard(engine):
    ac = sys.modules.get("agentcard")
    if getattr(engine.purchaser, "module", None) is None or ac is None:
        return {"wired": False}
    import requests
    tokens = json.loads(ac.TOKENS.read_text()) if ac.TOKENS.exists() else {}
    r = await asyncio.to_thread(lambda: requests.get(ac.BASE + "/buy/merchants", timeout=20,
                                                     headers={"Authorization": f"Bearer {ac.user_token()}"}))
    return {"wired": True, "status": r.status_code, "merchants": len((r.json() or {}).get("merchants", [])) if r.ok else None,
            "user_id": tokens.get("user_id"), "env": os.getenv("AGENTCARD_ENV") or "sandbox"}


async def probe_hosted(engine):
    escrow = engine.escrow
    if not hasattr(escrow, "source_type") or not getattr(escrow, "url", None):
        return {"wired": False}
    data = await escrow.get("payment", {"network": "Preprod", "limit": 10, "filterPaymentSourceType": escrow.source_type})
    rows = [p for p in (data.get("Payments") or data.get("PaymentRequests") or []) if p.get("agentIdentifier") == escrow.agent_identifier]
    return {"wired": True, "url": escrow.url, "payments": [{
        "id": p.get("id"), "state": p.get("onChainState"), "next": (p.get("NextAction") or {}).get("requestedAction"),
        "error": (p.get("NextAction") or {}).get("errorNote"), "tx": (p.get("CurrentTransaction") or {}).get("txHash"),
        "updated": p.get("updatedAt")} for p in rows]}


async def probe_sokosumi():
    async with httpx.AsyncClient(timeout=20) as client:
        rows = (await client.get(SOKOSUMI_AGENTS, params={"limit": 100})).json()
    rows = rows.get("data", rows) if isinstance(rows, dict) else rows
    match = next((a for a in rows if a.get("name") == SOKOSUMI_NAME), None)
    return {"listed": match is not None, "id": (match or {}).get("id"), "credits": (match or {}).get("credits"),
            "updated": (match or {}).get("updatedAt"), "agents_total": len(rows)}


async def probe_node():
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.get(V1_NODE_URL.rstrip("/") + "/health")
    return {"url": V1_NODE_URL, "status": r.status_code}


def job_rows(engine, limit=25):
    rows = []
    for job in engine.store.jobs()[-limit:][::-1]:
        events = engine.store.events(job["id"])
        outcome = job.get("outcome") or {}
        rows.append({"id": job["id"], "phase": job["phase"], "escrow_state": job["escrow_state"],
                     "ask": (job.get("input") or {}).get("ask"), "budget_usd": (job.get("input") or {}).get("max_total_usd"),
                     "purchase": {k: outcome.get(k) for k in ("status", "reason", "order_id", "total_usd", "merchant")} if outcome else None,
                     "settlement": engine.settlement(job), "simulated": job["simulated_escrow"] or job["simulated_purchase"],
                     "created": events[0]["at"] if events else None, "updated": events[-1]["at"] if events else None,
                     "last_message": events[-1]["message"] if events else None})
    return rows


def alerts(rows, card, probes, now):
    out = []
    if not card:
        out.append({"level": "warn", "text": "Card is OFF: every purchase fails as card_disabled and refunds the buyer"})
    for r in rows:
        if r["phase"] in ATTENTION_PHASES:
            out.append({"level": "error", "text": f"Job {r['id'][:8]} needs an operator: {r['phase']} — {r['last_message']} (check the card, then Resolve on its row)"})
        elif r["phase"] in OPEN_PHASES and r["updated"] and now - r["updated"] > STUCK_AFTER_S:
            hint = " — provider never settled; check the card for a charge, then Resolve on its row" if r["phase"] == "reconciling" else ""
            out.append({"level": "warn", "text": f"Job {r['id'][:8]} has been {r['phase']} for {int((now - r['updated']) / 60)} min{hint}"})
    for name, p in probes.items():
        if p and not p.get("ok"):
            out.append({"level": "error", "text": f"{name} check failed: {p.get('error')}"})
        elif name == "agentcard" and p.get("wired") and p.get("status") != 200:
            out.append({"level": "error", "text": f"Agentcard answered {p.get('status')}"})
        elif name == "sokosumi" and p.get("ok") and not p.get("listed"):
            out.append({"level": "warn", "text": f"{SOKOSUMI_NAME} is not in Sokosumi's listing right now"})
        elif name == "hosted" and p.get("wired"):
            for pay in p.get("payments", []):
                # An unfunded request that passed payByTime is a normal expiry, not an incident.
                if pay.get("error") and not (pay.get("state") == "FundsOrDatumInvalid" and "without on-chain lock" in pay["error"]):
                    out.append({"level": "error", "text": f"Hosted payment {str(pay.get('id'))[:8]}: {pay['error']}"})
    return out


class CardSwitch(BaseModel):
    enabled: bool
    note: str = Field(default="", max_length=200)


class Resolution(BaseModel):
    note: str = Field(min_length=3, max_length=200)


RESOLVABLE = {"reconciling", "manual_review"}


async def resolve_no_charge(engine, job_id, note):
    """Operator decision: the provider never settled (e.g. Agentcard `charge_status: unknown`) and the card shows no
    charge, so treat the purchase as a definitive failure and let the buyer's refund proceed. Never touches the provider."""
    async with engine.lock:
        job = engine.get(job_id)
        if job["phase"] not in RESOLVABLE:
            raise HTTPException(409, f"Only {sorted(RESOLVABLE)} jobs can be resolved; this one is {job['phase']}")
        job["outcome"] = {"status": "failed", "reason": "cancelled"}
        engine.change(job, "refund_due", f"Operator resolved as no charge: {note}")
    log = engine.store.get("ops", "log") or []
    log.append({"at": time.time(), "message": f"Job {job_id[:8]} resolved as no charge — {note}"})
    engine.store.put("ops", "log", log[-50:])
    return engine.public(job)


def register_ops(app, service, authorized, started_at=None):
    probes = Probes()
    started_at = started_at or time.time()
    headers = {"Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"}

    @app.get("/ops", include_in_schema=False)
    async def ops_page():
        return FileResponse(STATIC / "ops.html", headers=headers)

    @app.get("/ops.js", include_in_schema=False)
    async def ops_js():
        return FileResponse(STATIC / "ops.js", media_type="application/javascript")

    @app.get("/ops.css", include_in_schema=False)
    async def ops_css():
        return FileResponse(STATIC / "ops.css", media_type="text/css")

    @app.get("/ops/status", dependencies=[Depends(authorized)])
    async def ops_status(probe: bool = Query(default=True)):
        engine = service()
        now = time.time()
        card = card_enabled(engine.store)
        results = {}
        if probe:
            names = {"agentcard": lambda: probe_agentcard(engine), "hosted": lambda: probe_hosted(engine),
                     "sokosumi": probe_sokosumi, "node": probe_node}
            values = await asyncio.gather(*(probes.get(n, fn) for n, fn in names.items()))
            results = dict(zip(names, values))
        rows = job_rows(engine)
        from .hosted_escrow import HostedMasumiEscrow
        from .masumi_adapter import MasumiEscrow
        return {"now": now, "uptime_s": int(now - started_at),
                "config": {"mode": os.getenv("CARDANO_CARD_MODE", "local"), "purchase_backend": os.getenv("PURCHASE_BACKEND", "fake"),
                           "agentcard_env": os.getenv("AGENTCARD_ENV") or "sandbox",
                           "escrow_rail": ("hosted-v2" if isinstance(engine.escrow, HostedMasumiEscrow) else
                                           "self-hosted-v1" if isinstance(engine.escrow, MasumiEscrow) else "simulated"),
                           "agent_identifier": getattr(engine.escrow, "agent_identifier", None),
                           "seller_vkey": getattr(engine.escrow, "seller_vkey", None),
                           "fee": os.getenv("MASUMI_FEE_LOVELACE"), "path_prefixes": os.getenv("CARDANO_CARD_PATH_PREFIXES", "")},
                "card": {"enabled": card, **(engine.store.get("ops", "card") or {})},
                "ops_log": (engine.store.get("ops", "log") or [])[-10:][::-1],
                "jobs": rows, "events": engine.store.event_feed(None, 30)["events"][::-1],
                "probes": results, "alerts": alerts(rows, card, results, now)}

    @app.post("/ops/card", dependencies=[Depends(authorized)])
    async def ops_card(body: CardSwitch):
        return set_card(service().store, body.enabled, body.note)

    @app.post("/ops/jobs/{job_id}/resolve", dependencies=[Depends(authorized)])
    async def ops_resolve(job_id: str, body: Resolution):
        return await resolve_no_charge(service(), job_id, body.note)
