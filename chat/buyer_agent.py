"""Buyer agent behind the chat front door: hires Cardano Card over MIP-003 and pays its escrow from Mason's Preprod
buyer wallet. Two sellers:
- local (default): Mason's own Cardano Card in staged mode (quote first), paid in tADA through the 0.22 node with
  masumi's PreprodBuyer (idempotent funding and refund requests).
- V3_AGENT_URL set: the deployed, Sokosumi-listed CardanoCard (/v3, real card), paid 20 tUSDM through the 0.29 V2
  buyer node on :3002, reusing masumi/scripts/v2_hire.py's term checks and payment payload."""
import asyncio
import json
import os
import secrets
import sys
import time
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
S = dotenv_values(ROOT / "masumi/.env.preprod")  # local seller only; absent in the hosted deployment
DATA = Path(os.getenv("CHAT_DATA") or ROOT / "chat/data")
V3 = os.getenv("V3_AGENT_URL", "").rstrip("/")
SELLER = V3 or os.getenv("CARDANO_CARD_URL", "http://127.0.0.1:8787")
ADDRESS = json.loads(os.environ["DELIVERY_ADDRESS"])
FINAL = {"paid", "refunded", "result_submitted", "manual_review", "quote_rejected", "quote_expired",
         "payment_creation_unknown", "expired"}
# v1 purchase() on /v3 never empties Agentcard's single Amazon cart, so the ask says so (Ezra's 2026-10-07 lesson).
EMPTY_CART = "Start from an empty cart: remove anything already in it, then add only what this message asks for:"

store = Store(str(DATA / "buyer.db"))
seller = httpx.AsyncClient(base_url=SELLER, timeout=120)
if V3:
    sys.path.insert(0, str(ROOT / "masumi/scripts"))
    import v2_hire as v2
    # Node key from the environment when deployed; the laptop file otherwise.
    v2node = httpx.AsyncClient(base_url=v2.NODE + "/", timeout=60, headers={"token": os.getenv("V2_BUYER_NODE_KEY")
                               or dotenv_values(ROOT / "masumi/infra/masumi/.env")["ADMIN_KEY"]})
    FEE = "20 tUSDM"
else:
    node = httpx.AsyncClient(base_url=loopback_url(S["BUYER_PAYMENT_SERVICE_URL"]) + "/",
                             headers={"token": S["BUYER_PAYMENT_API_KEY"]}, timeout=30)
    routes = NodeRoutes(node)
    buyer = PreprodBuyer(store, node, S["AGENT_IDENTIFIER"], S["SELLER_VKEY"], create_masumi_input_hash,
                         digest({"buyer": S["BUYER_PAYMENT_SERVICE_URL"], "vkey": S["BUYER_VKEY"]}),
                         expected_funds=[{"unit": "", "amount": S["MASUMI_FEE_LOVELACE"]}])
    FEE = f"{int(S['MASUMI_FEE_LOVELACE']) / 1e6:g} tADA"


class Ask(BaseModel):
    ask: str
    max_usd: float


async def run():
    while True:
        for (job_id,) in store.db.execute("SELECT key FROM records WHERE namespace='chat_jobs'").fetchall():
            saved = store.get("chat_jobs", job_id)
            if saved["approved"] and not saved.get("done") and bool(saved.get("v3")) == bool(V3):
                try:
                    await (advance_v3 if saved.get("v3") else advance)(job_id, saved)
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
    if V3:
        request = {"identifier_from_purchaser": secrets.token_hex(13), "input_data": {
            # Agentcard only sees the ask, so the budget goes in it too; max_total_usd is just purchase()'s final check.
            "ask": f"{EMPTY_CART} {body.ask}, under ${body.max_usd:g} total. Pick the cheapest single item that fits the budget.",
            "max_total_usd": body.max_usd, **ADDRESS}}
        response = await seller.post("/start_job", json=request)
        response.raise_for_status()
        terms = response.json()
        try:
            payload = v2.purchase_payload(terms, request, v2.check_terms(terms, request))
        except SystemExit as exc:
            raise HTTPException(409, f"Refusing the agent's terms: {exc}") from None
        store.put("chat_jobs", terms["id"], {"request": request, "payload": payload, "approved": False, "v3": True})
        return {"job_id": terms["id"], "fee": FEE, "quoted": False}
    # The staged budget must cover the card's authorization ceiling (~$11 over the item price); the cap is in the ask.
    request = StartRequest(identifier_from_purchaser=secrets.token_hex(13), input_data={
        "ask": f"{body.ask} under ${body.max_usd:g}", "max_total_usd": body.max_usd + 12, "address": ADDRESS})
    response = await seller.post("/start_job", json=request.model_dump(mode="json"))
    response.raise_for_status()
    job_id = response.json()["id"]
    store.put("chat_jobs", job_id, {"request": request.model_dump(mode="json"), "approved": False})
    return {"job_id": job_id, "fee": FEE, "quoted": True}


@app.get("/jobs/{job_id}")
async def view(job_id: str):
    saved = store.get("chat_jobs", job_id) or {}
    status = await seller_status(job_id)
    return {"phase": status["phase"], "escrow_state": status["escrow_state"], "quote": status.get("quote"),
            "purchase": status.get("purchase"), "result": status.get("result"), "fee": FEE,
            "lock_txs": saved.get("lock_txs", []), "pay_error": saved.get("pay_error"),
            "events": status.get("events", []), "node": saved.get("node"),
            "fund": store.get("buyer_writes", "fund:" + job_id), "refund": store.get("buyer_writes", "refund:" + job_id)}


@app.post("/jobs/{job_id}/approve")
async def approve(job_id: str):
    saved = store.get("chat_jobs", job_id)
    if not saved or saved["approved"]:
        raise HTTPException(409, "Nothing waiting for approval")
    if saved.get("v3"):
        # Only pay while the agent still waits and the on-chain pay-by deadline leaves room for the lock to land.
        status = await seller_status(job_id)
        pay_by = int(saved["payload"]["payByTime"]) / 1000
        if status["phase"] != "awaiting_payment" or pay_by - time.time() < 180:
            raise HTTPException(409, "The payment window for this job has closed; ask again")
        # Checkpoint first: an escrow payment is never sent twice, even after a timeout.
        store.put("chat_jobs", job_id, {**saved, "approved": True})
        try:
            response = await v2node.post("purchase/", json=saved["payload"])
        except httpx.ConnectError:
            # Nothing reached the node, so the approval can be retried.
            store.put("chat_jobs", job_id, saved)
            raise HTTPException(502, "The buyer node is unreachable; try approving again") from None
        if response.status_code >= 400:
            store.put("chat_jobs", job_id, {**saved, "approved": True, "pay_error": response.text[:300]})
            raise HTTPException(502, "The buyer node refused the escrow payment")
        return {"approved": True}
    status = await seller_status(job_id)
    if status["phase"] != "awaiting_quote_approval":
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


async def advance_v3(job_id, saved):
    status = await seller_status(job_id)
    identifier = saved["payload"]["blockchainIdentifier"]
    response = await v2node.get("purchase/", params={"network": "Preprod", "limit": 25, "includeHistory": "true",
                                                     "filterPaymentSourceType": "Web3CardanoV2"})
    response.raise_for_status()
    data = response.json().get("data", {})
    purchase = next((p for p in data.get("Purchases") or [] if p.get("blockchainIdentifier") == identifier), {})
    lock = (purchase.get("CurrentTransaction") or {}).get("txHash")
    saved["node"] = {"state": purchase.get("onChainState"), "action": (purchase.get("NextAction") or {}).get("requestedAction"),
                     "error": (purchase.get("NextAction") or {}).get("errorNote"), "tx": lock}
    if lock and not saved.get("lock_txs") and purchase.get("onChainState") == "FundsLocked":
        saved["lock_txs"] = [lock]
    if status["phase"] == "refund_due" and (status.get("purchase") or {}).get("status") == "failed" and not saved.get("refund_requested"):
        saved["refund_requested"] = True
        store.put("chat_jobs", job_id, saved)
        try:
            (await v2node.post("purchase/request-refund", json={"network": "Preprod", "blockchainIdentifier": identifier})).raise_for_status()
        except Exception:
            # Not sent or refused (lock not settled yet, node busy): try again on the next pass.
            saved["refund_requested"] = False
            store.put("chat_jobs", job_id, saved)
            raise
    saved["done"] = status["phase"] in FINAL and status["phase"] != "result_submitted"
    store.put("chat_jobs", job_id, saved)
