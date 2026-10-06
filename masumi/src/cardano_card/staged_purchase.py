"""Versioned boundary for Mason's prepare/confirm/inspect adapter.

No legacy purchase() fallback. Unknown writes are inspected, never retried.
Amounts are integer USD cents; customer escrow is a separate fixed test-ADA price.
"""
import asyncio
import importlib
import time
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt

from .models import digest


class QuoteItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=300)
    quantity: StrictInt = Field(gt=0, le=1000)


class PreparedQuote(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["prepared"]
    job_id: str
    quote_id: str = Field(min_length=1, max_length=200)
    conversation_id: str = Field(min_length=1, max_length=200)
    cart_hash: str = Field(min_length=1, max_length=500)
    currency: Literal["USD"]
    subtotal_cents: StrictInt = Field(ge=0)
    estimated_total_cents: StrictInt = Field(ge=0)
    authorization_ceiling_cents: StrictInt = Field(gt=0)
    expires_at: StrictInt = Field(gt=0)
    merchant: str = Field(min_length=1, max_length=200)
    items: list[QuoteItem] = Field(min_length=1)
    payment_source: Literal["vault"] = "vault"


class CheckoutOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["confirmed", "failed_no_purchase", "pending", "partial", "approval_required", "needs_input"]
    job_id: str
    quote_id: str
    conversation_id: str
    cart_hash: str
    payment_source: Literal["vault"]
    currency: Literal["USD"]
    merchant_confirmed: bool = Field(default=False, strict=True)
    no_purchase: bool = Field(default=False, strict=True)
    charge_status: Literal["none", "unknown", "authorized", "captured"] = "unknown"
    order_id: str | None = Field(default=None, min_length=1, max_length=200)
    total_cents: StrictInt | None = Field(default=None, ge=0)
    merchant: str | None = Field(default=None, min_length=1, max_length=200)
    items: list[dict] = Field(default_factory=list)
    reason: str | None = None
    approval_url: str | None = None


class StagedModule:
    """Trusted local module; importing it must not execute provider operations."""
    simulated = False

    def __init__(self, module_name):
        self.module = importlib.import_module(module_name)
        if getattr(self.module, "PURCHASE_PROTOCOL_VERSION", None) != 2:
            raise ValueError("Mason adapter must declare PURCHASE_PROTOCOL_VERSION = 2")
        for method in ("prepare_purchase", "confirm_purchase", "inspect_purchase"):
            if not callable(getattr(self.module, method, None)):
                raise ValueError("Mason adapter is missing a staged purchase method")

    async def prepare_purchase(self, job_id, intent, max_total_usd, address):
        # Decimal text avoids rounding a USD ceiling through float.
        return await asyncio.to_thread(self.module.prepare_purchase, job_id=job_id, intent=intent,
                                       max_total_usd=max_total_usd, address=address)

    async def confirm_purchase(self, job_id, quote_id):
        return await asyncio.to_thread(self.module.confirm_purchase, job_id=job_id, quote_id=quote_id)

    async def inspect_purchase(self, job_id):
        return await asyncio.to_thread(self.module.inspect_purchase, job_id=job_id)


class FakeStagedModule:
    simulated = True
    SCENARIOS = {"success", "declined", "over_budget", "unknown", "partial"}

    def __init__(self, store, scenario="success", clock=time.time):
        if scenario not in self.SCENARIOS:
            raise ValueError("Unsupported staged simulation scenario")
        self.store, self.scenario, self.clock = store, scenario, clock

    async def prepare_purchase(self, job_id, intent, max_total_usd, address):
        existing = self.store.get("staged_fake", job_id)
        if existing:
            return existing["quote"]
        scenario = (self.store.get("demo_scenarios", job_id) or {}).get("scenario", self.scenario)
        quote = {"status": "prepared", "job_id": job_id, "quote_id": "SIM-QUOTE-" + job_id,
                 "conversation_id": "SIM-CONV-" + job_id, "cart_hash": digest({"job": job_id, "intent": intent}),
                 "currency": "USD", "subtotal_cents": 600, "estimated_total_cents": 750,
                 "authorization_ceiling_cents": 1200 if scenario == "over_budget" else 900,
                 "expires_at": int(self.clock()) + 900, "merchant": "Simulated merchant",
                 "items": [{"name": "Simulated item", "quantity": 1}], "payment_source": "vault"}
        self.store.put("staged_fake", job_id, {"quote": quote, "scenario": scenario, "confirmations": 0})
        return quote

    async def confirm_purchase(self, job_id, quote_id):
        record = self.store.get("staged_fake", job_id)
        if record["quote"]["quote_id"] != quote_id:
            raise ValueError("Quote mismatch")
        if record.get("outcome"):
            return record["outcome"]
        record["confirmations"] += 1
        quote = record["quote"]
        outcome = {key: quote[key] for key in ("job_id", "quote_id", "conversation_id", "cart_hash", "currency", "payment_source")}
        scenario = record["scenario"]
        if scenario == "declined":
            outcome.update(status="failed_no_purchase", no_purchase=True, charge_status="none")
        elif scenario in {"unknown", "partial"}:
            outcome.update(status="partial" if scenario == "partial" else "pending")
        else:
            outcome.update(status="confirmed", merchant_confirmed=True, charge_status="captured",
                           order_id="SIM-ORDER-" + job_id, total_cents=750, merchant=quote["merchant"], items=quote["items"])
        record["outcome"] = outcome
        self.store.put("staged_fake", job_id, record)
        return outcome

    async def inspect_purchase(self, job_id):
        record = self.store.get("staged_fake", job_id)
        if not record:
            return None
        return record.get("outcome") or record["quote"]


class CoordinatedPurchaser:
    """Adapts staged results to the coordinator's existing conservative outcome model."""
    def __init__(self, store, module, clock=time.time):
        self.store, self.module, self.clock = store, module, clock
        self.simulated = module.simulated

    @staticmethod
    def pending(message="Purchase requires read-only reconciliation"):
        return {"status": "pending", "reason": "unknown", "message": message}

    async def purchase(self, inputs, request_id, response=None):
        job = self.store.get("jobs", request_id)
        quote = job["quote"]
        # The coordinator alone can supply authorization. Never accept it from provider prose.
        saved_terms = {k: v for k, v in quote.items() if k != "terms_hash"}
        if job.get("approved_quote_hash") != quote["terms_hash"] or digest(saved_terms) != quote["terms_hash"]:
            return self.pending("The saved quote has no matching customer approval")
        if self.store.get("staged_confirmations", request_id):
            return await self.inspect(request_id)
        if self.clock() >= quote["expires_at"]:
            return {"status": "failed", "reason": "cancelled"}
        if digest(inputs) != quote["purchase_input_hash"]:
            return self.pending("Purchase inputs differ from the approved quote")
        self.store.put("staged_confirmations", request_id, {"quote_hash": quote["terms_hash"], "requested": True})
        raw = await self.module.confirm_purchase(job_id=request_id, quote_id=quote["quote_id"])
        return self.normalize(job, raw)

    async def inspect(self, request_id):
        job = self.store.get("jobs", request_id)
        return self.normalize(job, await self.module.inspect_purchase(job_id=request_id))

    def normalize(self, job, raw):
        try:
            outcome = CheckoutOutcome.model_validate(raw)
            quote = job["quote"]
            if any(getattr(outcome, key) != quote[key] for key in
                   ("job_id", "quote_id", "conversation_id", "cart_hash", "currency", "payment_source")):
                return self.pending("Checkout evidence does not match the approved cart")
            prior = self.store.get("staged_purchase_evidence", job["id"]) or {}
            possible_purchase = (prior.get("possible_purchase", False) or outcome.status == "partial"
                                 or bool(outcome.order_id) or outcome.charge_status in {"authorized", "captured"}
                                 or outcome.merchant_confirmed)
            self.store.put("staged_purchase_evidence", job["id"], {
                "quote_hash": quote["terms_hash"], "possible_purchase": possible_purchase,
                "order_id": outcome.order_id or prior.get("order_id"),
                "last_status": outcome.status, "charge_status": outcome.charge_status})
            if outcome.status == "failed_no_purchase":
                if not possible_purchase and outcome.no_purchase and outcome.charge_status == "none" and not outcome.order_id and not outcome.merchant_confirmed:
                    return {"status": "failed", "reason": "declined"}
            elif outcome.status == "confirmed":
                if (outcome.merchant_confirmed and not outcome.no_purchase and outcome.order_id and outcome.merchant
                        and outcome.items and outcome.total_cents is not None
                        and outcome.charge_status in {"authorized", "captured"}
                        and outcome.total_cents <= quote["authorization_ceiling_cents"]):
                    return {"status": "success", "order_id": outcome.order_id,
                            "total_usd": str(Decimal(outcome.total_cents) / 100), "merchant": outcome.merchant, "items": outcome.items}
            elif outcome.status == "approval_required":
                if outcome.approval_url and outcome.approval_url.startswith("https://"):
                    return {"status": "pending", "reason": "approval_required", "conversation_id": outcome.conversation_id,
                            "approval_url": outcome.approval_url,
                            "message": "Complete AgentCard approval, then continue to inspect this purchase"}
            elif outcome.status == "pending":
                return {"status": "pending", "reason": "processing", "message": "Waiting for authoritative merchant confirmation"}
            # Partial outcomes and further input cannot safely restart/modify this funded quote.
            return self.pending("Partial or unresolved checkout requires reconciliation; no automatic refund")
        except Exception:
            return self.pending("Unrecognized or mismatched checkout evidence; no automatic refund")
