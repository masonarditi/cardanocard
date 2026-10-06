"""Provider boundaries; fake implementations never call the network."""
from __future__ import annotations

import asyncio
import importlib
import time
from decimal import Decimal
from typing import Protocol

from .models import PurchaseInput, digest
from .store import Store


class Escrow(Protocol):
    simulated: bool
    async def create(self, job: dict) -> dict: ...
    async def observe(self, job: dict) -> str: ...
    async def submit(self, job: dict, result: str) -> None: ...
    async def authorize_refund(self, job: dict) -> None: ...


class Purchaser(Protocol):
    simulated: bool
    async def purchase(self, inputs: dict, request_id: str, response: dict | None = None) -> dict: ...
    async def inspect(self, request_id: str) -> dict: ...


class FakeEscrow:
    simulated = True

    def __init__(self, store: Store):
        self.store = store

    async def create(self, job):
        now = int(time.time())
        result = {"blockchainIdentifier": "SIM-" + job["id"], "payByTime": now + 600,
                  "submitResultTime": now + 3600, "unlockTime": now + 7200,
                  "externalDisputeUnlockTime": now + 10800, "agentIdentifier": "SIM-cardano-card",
                  "sellerVKey": "SIM-seller", "inputHash": digest(job["input"])}
        self.store.put("escrow", job["id"], {"state": "AwaitingPayment"})
        return result

    async def observe(self, job):
        return self.store.get("escrow", job["id"])["state"]

    async def submit(self, job, result):
        self.store.put("escrow", job["id"], {"state": "ResultSubmitted", "result": result})

    async def authorize_refund(self, job):
        self.store.put("escrow", job["id"], {"state": "RefundWithdrawn"})

    def action(self, job, action):
        current = self.store.get("escrow", job["id"])
        transitions = {"fund": ("AwaitingPayment", "FundsLocked"),
                       "request_refund": ("FundsLocked", "RefundRequested"),
                       "withdraw": ("ResultSubmitted", "Withdrawn")}
        old, new = transitions[action]
        if current["state"] != old:
            raise ValueError("Simulation action is not allowed in this escrow state")
        if action == "fund" and time.time() >= job["payment"]["payByTime"]:
            raise ValueError("Payment deadline has passed")
        current["state"] = new
        self.store.put("escrow", job["id"], current)


class FakePurchaser:
    simulated = True
    SCENARIOS = {"success", "declined", "over_budget", "approval", "needs_input", "unknown"}

    def __init__(self, store: Store, scenario="success"):
        if scenario not in self.SCENARIOS:
            raise ValueError("Unsupported fake scenario")
        self.store, self.scenario = store, scenario

    def _success(self, request_id):
        return {"status": "success", "order_id": "SIM-ORDER-" + request_id,
                "total_usd": "7.50", "merchant": "Simulated merchant",
                "items": [{"name": "Simulated item", "quantity": 1}]}

    async def purchase(self, inputs, request_id, response=None):
        fingerprint = digest(inputs)
        old = self.store.get("purchases", request_id)
        if old and old["fingerprint"] != fingerprint:
            raise ValueError("Changed purchase inputs under an existing request ID")
        if old and (old["outcome"]["status"] != "pending" or response is None):
            return old["outcome"]
        scenario = old["scenario"] if old else (self.store.get("demo_scenarios", request_id) or {}).get("scenario", self.scenario)
        budget = Decimal(inputs["max_total_usd"])
        if budget < Decimal("7.50") or scenario == "over_budget":
            outcome = {"status": "failed", "reason": "over_budget"}
        elif scenario == "declined":
            outcome = {"status": "failed", "reason": "declined"}
        elif response is not None:
            outcome = ({"status": "failed", "reason": "cancelled"} if response.get("approved") is False
                       else self._success(request_id))
        elif scenario in {"approval", "needs_input", "unknown"}:
            reason = {"approval": "approval_required", "needs_input": "needs_input", "unknown": "unknown"}[scenario]
            outcome = {"status": "pending", "reason": reason, "conversation_id": "SIM-CONV-" + request_id,
                       "message": "Simulated " + reason}
        else:
            outcome = self._success(request_id)
        self.store.put("purchases", request_id, {"fingerprint": fingerprint, "scenario": scenario, "outcome": outcome})
        return outcome

    async def inspect(self, request_id):
        saved = self.store.get("purchases", request_id)
        return saved["outcome"] if saved else {"status": "pending", "reason": "unknown", "message": "No saved purchase evidence"}


class ModulePurchaser:
    """Trusted local Mason module. Inspect MUST be read-only; purchase MUST deduplicate."""
    simulated = False

    def __init__(self, module_name):
        self.module = importlib.import_module(module_name)
        if not callable(getattr(self.module, "purchase", None)) or not callable(getattr(self.module, "inspect_purchase", None)):
            raise ValueError("Mason module must implement purchase() and read-only inspect_purchase()")

    async def purchase(self, inputs, request_id, response=None):
        validated = PurchaseInput.model_validate(inputs)
        kwargs = {"request_id": request_id}
        if response is not None:
            kwargs["response"] = response
        # No outer timeout: cancelling a thread cannot stop an in-flight card charge.
        # Mason must bound provider I/O and return pending:unknown after ambiguous timeouts.
        return await asyncio.to_thread(self.module.purchase, validated.ask, float(validated.max_total_usd),
                                       validated.address.model_dump(), **kwargs)

    async def inspect(self, request_id):
        return await asyncio.to_thread(self.module.inspect_purchase, request_id=request_id)
