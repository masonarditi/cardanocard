"""Buyer agent behind the chat front door: hires Cardano Card over MIP-003 and pays its escrow from Mason's
Preprod buyer wallet, reusing masumi's PreprodBuyer (idempotent funding and refund requests)."""
import asyncio
import json
import os
import secrets
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from dotenv import dotenv_values
from fastapi import FastAPI, HTTPException
from masumi.helper_functions import create_masumi_input_hash
from pydantic import BaseModel

from cardano_card.acceptance import NodeRoutes
from cardano_card.models import StartRequest, digest
from cardano_card.preprod_buyer import PreprodBuyer, loopback_url
from cardano_card.store import Store

ROOT = Path(__file__).resolve().parents[1]
S = dotenv_values(ROOT / "masumi/.env.preprod")
SELLER = os.getenv("CARDANO_CARD_URL", "http://127.0.0.1:8787")
ADDRESS = json.loads(os.environ["DELIVERY_ADDRESS"])
FINAL = {"paid", "refunded", "result_submitted", "manual_review", "quote_rejected", "quote_expired",
         "payment_creation_unknown", "expired"}

store = Store(str(ROOT / "chat/data/buyer.db"))
node = httpx.AsyncClient(base_url=loopback_url(S["BUYER_PAYMENT_SERVICE_URL"]) + "/",
                         headers={"token": S["BUYER_PAYMENT_API_KEY"]}, timeout=30)
seller = httpx.AsyncClient(base_url=SELLER, timeout=30)
routes = NodeRoutes(node)
buyer = PreprodBuyer(store, node, S["AGENT_IDENTIFIER"], S["SELLER_VKEY"], create_masumi_input_hash,
                     digest({"buyer": S["BUYER_PAYMENT_SERVICE_URL"], "vkey": S["BUYER_VKEY"]}),
                     expected_funds=[{"unit": "", "amount": S["MASUMI_FEE_LOVELACE"]}])


class Ask(BaseModel):
    ask: str
    max_total_usd: float


async def run():
    while True:
        for (job_id,) in store.db.execute("SELECT key FROM records WHERE namespace='chat_jobs'").fetchall():
            saved = store.get("chat_jobs", job_id)
            if saved["approved"] and not saved.get("done"):
                try:
                    await advance(job_id, saved)
                except Exception as exc:
                    print(f"WAIT | {job_id} | {type(exc).__name__}: {str(exc)[:160]}", flush=True)
        await asyncio.sleep(5)


@asynccontextmanager
async def lifespan(_):
    task = asyncio.create_task(run())
    yield
    task.cancel()


app = FastAPI(lifespan=lifespan)


async def seller_status(job_id):
    response = await seller.get("/status", params={"job_id": job_id})
    response.raise_for_status()
    return response.json()


@app.post("/jobs")
async def hire(body: Ask):
    request = StartRequest(identifier_from_purchaser=secrets.token_hex(13),
                           input_data={"ask": body.ask, "max_total_usd": body.max_total_usd, "address": ADDRESS})
    response = await seller.post("/start_job", json=request.model_dump(mode="json"))
    response.raise_for_status()
    job_id = response.json()["id"]
    store.put("chat_jobs", job_id, {"request": request.model_dump(mode="json"), "approved": False})
    return {"job_id": job_id}


@app.get("/jobs/{job_id}")
async def view(job_id: str):
    saved = store.get("chat_jobs", job_id) or {}
    status = await seller_status(job_id)
    return {"phase": status["phase"], "escrow_state": status["escrow_state"], "quote": status.get("quote"),
            "purchase": status.get("purchase"), "result": status.get("result"),
            "fee_lovelace": int(S["MASUMI_FEE_LOVELACE"]), "lock_txs": saved.get("lock_txs", []),
            "fund": store.get("buyer_writes", "fund:" + job_id), "refund": store.get("buyer_writes", "refund:" + job_id)}


@app.post("/jobs/{job_id}/approve")
async def approve(job_id: str):
    saved = store.get("chat_jobs", job_id)
    status = await seller_status(job_id)
    if not saved or status["phase"] != "awaiting_quote_approval":
        raise HTTPException(409, "No quote waiting for approval")
    response = await seller.post("/provide_input", json={"job_id": job_id, "input_schema_hash": status["input_schema_hash"],
                                                          "input_data": {"approved": True}})
    response.raise_for_status()
    store.put("chat_jobs", job_id, {**saved, "approved": True})
    return {"approved": True}


async def advance(job_id, saved):
    status = await seller_status(job_id)
    if status.get("payment") and not saved.get("payment"):
        saved["payment"] = status["payment"]
    if status["phase"] == "awaiting_payment" and saved.get("payment"):
        await buyer.fund(saved["request"], {"id": job_id, "simulated_escrow": False, **saved["payment"]})
    if saved.get("payment") and store.get("buyer_writes", "fund:" + job_id):
        observed = await routes.observe_buyer({"payment": saved["payment"]}, S["BUYER_VKEY"])
        if observed:
            saved["lock_txs"] = observed["tx_hashes"]
    if status["phase"] == "refund_due":
        await buyer.refund(job_id, status)
    saved["done"] = status["phase"] in FINAL
    store.put("chat_jobs", job_id, saved)
