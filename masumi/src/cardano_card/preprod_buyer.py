"""Gated local Preprod buyer. V1 wire format follows the pinned Masumi SDK.

No live compatibility is claimed. A saved write attempt is never automatically
repeated, including after a timeout or process restart.
"""
import argparse
import asyncio
import hashlib
import json
import logging
import os
import time
from pathlib import Path
from urllib.parse import urlparse

import httpx
from dotenv import load_dotenv

from .models import StartRequest, digest
from .store import Store

TIMES = ("payByTime", "submitResultTime", "unlockTime", "externalDisputeUnlockTime")


def loopback_url(value):
    parsed = urlparse(value)
    if (parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost"}
            or parsed.username or parsed.password or parsed.query or parsed.fragment):
        raise ValueError("This buyer only connects to local HTTP services")
    return value.rstrip("/")


class PreprodBuyer:
    def __init__(self, store, client, agent_identifier, seller_vkey, input_hasher, identity, clock=time.time, expected_funds=None):
        if not all((agent_identifier, seller_vkey, identity)):
            raise ValueError("Configure the expected seller, agent, and buyer service")
        self.store, self.client = store, client
        self.agent_identifier, self.seller_vkey = agent_identifier, seller_vkey
        self.input_hasher, self.identity, self.clock = input_hasher, identity, clock
        self.expected_funds = expected_funds
        self.lock = asyncio.Lock()

    def payment_payload(self, request, terms):
        validated = StartRequest.model_validate(request)
        if terms.get("simulated_escrow") is not False:
            raise ValueError("Preprod buyer refuses simulated or unlabelled escrow")
        if terms.get("agentIdentifier") != self.agent_identifier or terms.get("sellerVKey") != self.seller_vkey:
            raise ValueError("Payment terms do not match the configured agent and seller")
        expected = self.input_hasher(validated.input_data, validated.identifier_from_purchaser)
        if terms.get("inputHash") != expected:
            raise ValueError("Payment input hash does not match the exact requested inputs")
        from .masumi_adapter import MasumiEscrow
        funds = MasumiEscrow.funds(terms.get("RequestedFunds"))
        if self.expected_funds is None or funds != MasumiEscrow.funds(self.expected_funds):
            raise ValueError("Service fee differs from the configured test budget")
        if not isinstance(terms.get("blockchainIdentifier"), str) or not terms["blockchainIdentifier"] or terms["blockchainIdentifier"].startswith("SIM-"):
            raise ValueError("Missing real escrow identifier")
        times = terms.get("rawTimes")
        if not isinstance(times, dict) or set(times) != set(TIMES):
            raise ValueError("Exact node deadline values are required")
        raw = [str(times[key]) for key in TIMES]
        if any(not stamp.isdigit() for stamp in raw):
            raise ValueError("Invalid deadline value")
        units = {len(stamp) > 11 for stamp in raw}
        if len(units) != 1:
            raise ValueError("Mixed timestamp units")
        normalized = [int(stamp) // 1000 if int(stamp) > 100_000_000_000 else int(stamp) for stamp in raw]
        if not self.clock() + 60 < normalized[0] < normalized[1] <= normalized[2] <= normalized[3]:
            raise ValueError("Payment deadline expired or invalid")
        return {"identifierFromPurchaser": validated.identifier_from_purchaser,
                "network": "Preprod", "sellerVkey": self.seller_vkey, "paymentType": "Web3CardanoV1",
                "blockchainIdentifier": terms["blockchainIdentifier"], "agentIdentifier": self.agent_identifier,
                # V1 resolves fixed pricing from the registry and rejects Amounts.
                # The requested amount is still checked against our budget above.
                "inputHash": expected, **dict(zip(TIMES, raw))}

    async def _once(self, key, route, payload):
        async with self.lock:
            fingerprint = digest({"route": route, "payload": payload, "buyer": self.identity})
            previous = self.store.get("buyer_writes", key)
            if previous:
                if previous["fingerprint"] != fingerprint:
                    raise ValueError("Saved payment operation differs; do not change or repay it")
                return previous
            record = {"fingerprint": fingerprint, "state": "unknown", "route": route,
                      "blockchain_identifier": payload["blockchainIdentifier"], "attempted_at": self.clock()}
            self.store.put("buyer_writes", key, record)
            try:
                response = await self.client.post(route, json=payload)
                record["http_status"] = response.status_code
                record["diagnostic"] = "http_error" if response.is_error else "unconfirmed_response"
                if response.is_error:
                    # Keep the node's own error text (no payload or credentials) so a rejection can be diagnosed.
                    try:
                        record["node_error"] = str((response.json().get("error") or {}).get("message"))[:300]
                    except Exception:
                        pass
                self.store.put("buyer_writes", key, record)
                response.raise_for_status()
                data = response.json()
                if data.get("status") != "success":
                    return record
                # Accepted by node is not on-chain confirmation. Preserve only nonsecret metadata.
                record["state"] = "accepted_by_node"
                record["diagnostic"] = "accepted_by_node"
                self.store.put("buyer_writes", key, record)
            except Exception:
                # Never persist provider bodies or exception text: either can
                # contain credentials. An HTTP error alone does not prove that
                # no write occurred; reconcile before creating another payment.
                record.setdefault("diagnostic", "transport_or_response_error")
                self.store.put("buyer_writes", key, record)
            return record

    async def fund(self, request, terms):
        # A duplicate is checked before deadline validation so a later inspection remains possible.
        key = "fund:" + terms["id"]
        binding = digest({"request": request, "terms": terms, "buyer": self.identity})
        saved = self.store.get("buyer_bindings", terms["id"])
        if saved:
            if saved["binding"] != binding:
                raise ValueError("Saved job terms changed")
            previous = self.store.get("buyer_writes", key)
            if previous:
                return previous
        payload = self.payment_payload(request, terms)
        self.store.put("buyer_bindings", terms["id"], {"binding": binding, "payload": payload, "buyer": self.identity})
        return await self._once(key, "/purchase/", payload)

    async def refund(self, job_id, status):
        saved = self.store.get("buyer_bindings", job_id)
        if not saved or saved.get("buyer") != self.identity or not self.store.get("buyer_writes", "fund:" + job_id):
            raise ValueError("No saved funding attempt for this job")
        if status.get("id") != job_id or status.get("simulated_escrow") is not False:
            raise ValueError("Job identity or escrow mode does not match")
        if status.get("phase") != "refund_due" or (status.get("purchase") or {}).get("status") != "failed":
            raise ValueError("Refund requires a definitive no-purchase failure")
        return await self._once("refund:" + job_id, "/purchase/request-refund", {
            "network": "Preprod", "blockchainIdentifier": saved["payload"]["blockchainIdentifier"]})


async def run(args):
    if os.getenv("MASUMI_V1_COMPATIBLE") != "true":
        raise ValueError("Validate and record V1 node compatibility before enabling this buyer")
    service_url = loopback_url(os.environ["BUYER_PAYMENT_SERVICE_URL"])
    agent_url = loopback_url(args.url)
    buyer_key, caller_token = os.environ["BUYER_PAYMENT_API_KEY"], os.environ["CARDANO_CARD_TOKEN"]
    if not buyer_key or not caller_token:
        raise ValueError("Configure separate buyer-node and agent caller credentials")
    logging.getLogger("masumi.helper_functions").setLevel(logging.CRITICAL)
    from masumi.helper_functions import create_masumi_input_hash
    logging.getLogger("masumi.helper_functions").setLevel(logging.CRITICAL)
    store = Store(args.database)
    try:
        # Trailing slash plus relative routes preserve /api/v1 in the node URL.
        async with httpx.AsyncClient(base_url=agent_url, headers={"Authorization": "Bearer " + caller_token}, timeout=30) as agent, \
                   httpx.AsyncClient(base_url=service_url + "/", headers={"token": buyer_key}, timeout=30) as node:
            info = await agent.get("/availability")
            info.raise_for_status()
            if info.json().get("simulated_escrow") is not False or info.json().get("simulated_purchase") is not True:
                raise ValueError("This milestone requires Preprod escrow and simulated merchant purchases")
            class NodeRoutes:
                async def post(self, route, **kwargs):
                    return await node.post(route.lstrip("/"), **kwargs)
            buyer = PreprodBuyer(store, NodeRoutes(), os.environ["AGENT_IDENTIFIER"], os.environ["SELLER_VKEY"],
                                 create_masumi_input_hash, hashlib.sha256((service_url + buyer_key).encode()).hexdigest(),
                                 expected_funds=[{"unit":"", "amount":os.environ["MASUMI_FEE_LOVELACE"]}])
            if args.action == "start":
                request = StartRequest.model_validate(json.loads(Path(args.target).read_text())).model_dump(mode="json")
                caller = request["identifier_from_purchaser"]
                prior = store.get("buyer_requests", caller)
                if prior and prior["request"] != request:
                    raise ValueError("Request ID already has different inputs")
                if prior and prior.get("terms"):
                    print(json.dumps({"job_id": prior["terms"]["id"], "phase": "saved_job", "next": "status"}))
                    return
                store.put("buyer_requests", caller, {"request": request})
                result = await agent.post("/start_job", json=request)
                result.raise_for_status()
                terms = result.json()
                store.put("buyer_jobs", terms["id"], {"request": request, "terms": terms})
                store.put("buyer_requests", caller, {"request": request, "terms": terms})
                print(json.dumps({"job_id": terms["id"], "phase": terms["phase"], "next": "fund"}))
                return
            saved = store.get("buyer_jobs", args.target)
            if not saved:
                raise ValueError("No saved job. Use start with a request JSON file first")
            if args.action == "fund":
                result = await buyer.fund(saved["request"], saved["terms"])
            else:
                response = await agent.get("/status", params={"job_id": args.target})
                response.raise_for_status()
                status = response.json()
                result = await buyer.refund(args.target, status) if args.action == "refund" else {
                    key: status[key] for key in ("id", "phase", "escrow_state", "simulated_escrow", "simulated_purchase")}
            print(json.dumps(result, indent=2))
    finally:
        store.close()


def main():
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("start", "fund", "status", "refund"))
    parser.add_argument("target", help="Request JSON file for start; saved job ID otherwise")
    parser.add_argument("--url", default="http://127.0.0.1:8080")
    parser.add_argument("--database", default="data/preprod-buyer.db")
    args = parser.parse_args()
    try:
        asyncio.run(run(args))
    except Exception:
        # Provider exceptions can contain request headers/payloads; do not print them.
        raise SystemExit("Buyer operation did not complete. Check local configuration and saved state; do not retry an uncertain payment with a new ID.") from None


if __name__ == "__main__":
    main()
