"""Quote approval -> fixed Preprod escrow -> staged Vault checkout.

The fixed escrow price is test ADA, not a USD exchange rate or actual reimbursement.
Existing legacy acceptance scenarios remain available through Engine.
"""
import time
import uuid
from decimal import Decimal

from .engine import Conflict, Engine
from .models import digest, parse_purchase_input
from .staged_purchase import CoordinatedPurchaser, PreparedQuote


class StagedEngine(Engine):
    def __init__(self, store, escrow, module, *, escrow_lovelace, payout_address, clock=time.time):
        if type(escrow_lovelace) is not int or escrow_lovelace <= 0:
            raise ValueError("A positive fixed test-ADA escrow price is required")
        if not payout_address:
            raise ValueError("An explicit payout destination is required")
        if not escrow.simulated:
            from .chain_evidence import _address_bytes
            _address_bytes(payout_address)
            if getattr(escrow, "payout_address", None) != payout_address:
                raise ValueError("Escrow and staged coordinator must bind the same payout destination")
        binding = {"simulated_escrow": escrow.simulated, "simulated_purchase": module.simulated,
                   "payout_address": payout_address, "escrow_lovelace": escrow_lovelace,
                   "module": getattr(getattr(module, "module", None), "__name__", type(module).__name__),
                   "seller_vkey": getattr(escrow, "seller_vkey", None),
                   "agent_identifier": getattr(escrow, "agent_identifier", None),
                   "node_url": getattr(escrow, "url", None)}
        previous = store.get("staged_runtime", "binding")
        if previous and previous != binding:
            raise ValueError("Staged database belongs to another provider/recipient configuration; resume its original runtime")
        for job in store.jobs():
            if job.get("purchase_protocol") == 2 and (job["simulated_escrow"] != escrow.simulated
                                                      or job["simulated_purchase"] != module.simulated):
                raise ValueError("Cannot reinterpret saved simulation as live purchasing")
        store.put("staged_runtime", "binding", binding)
        self.module = module
        self.escrow_lovelace, self.payout_address = escrow_lovelace, payout_address
        super().__init__(store, escrow, CoordinatedPurchaser(store, module, clock), clock)

    async def start(self, request):
        async with self.lock:
            inputs = parse_purchase_input(request.input_data).model_dump(mode="json")
            existing = next((j for j in self.store.jobs() if j["caller_id"] == request.identifier_from_purchaser), None)
            if existing:
                if digest(existing["wire_input"]) != digest(request.input_data) or existing.get("purchase_protocol") != 2:
                    raise Conflict("Request ID already belongs to different inputs or purchase protocol")
                return existing
            job = {"id": str(uuid.uuid4()), "caller_id": request.identifier_from_purchaser,
                   "input": inputs, "wire_input": request.input_data, "phase": "quote_queued", "payment": None,
                   "escrow_state": "AwaitingPayment", "outcome": None, "result": None,
                   "purchase_started": False, "response": None, "input_schema": None,
                   "input_schema_hash": None, "approval_revision": 0, "purchase_protocol": 2,
                   "quote": None, "approved_quote_hash": None,
                   "quote_terms": {"requested_funds": [{"unit": "", "amount": str(self.escrow_lovelace)}],
                                   "payout_address": self.payout_address, "network": "Preprod"},
                   "simulated_escrow": self.escrow.simulated, "simulated_purchase": self.module.simulated}
            self.store.save_job(job, "Prompt saved; cart preparation cannot spend or request escrow funding")
            return job

    async def prepare(self, job):
        self.change(job, "preparing_quote", "Preparing a cart without checkout; saved request before provider call")
        try:
            raw = await self.module.prepare_purchase(job_id=job["id"], intent=job["input"]["ask"],
                       max_total_usd=job["input"]["max_total_usd"], address=job["input"]["address"])
            self.accept_quote(job, raw)
        except Exception:
            self.change(job, "quote_reconciling", "Preparation outcome uncertain; inspect the same job without repeating preparation")

    def accept_quote(self, job, raw):
        # Preparation never spends, so a definitive no-cart answer ends the job before any escrow exists.
        if isinstance(raw, dict) and raw.get("job_id") == job["id"] and raw.get("status") in {"failed_no_purchase", "needs_input"}:
            self.change(job, "quote_rejected", "No usable cart was prepared; no payment or checkout")
            return
        try:
            quote = PreparedQuote.model_validate(raw).model_dump()
            ceiling = quote["authorization_ceiling_cents"]
            if quote["job_id"] != job["id"]:
                raise ValueError("Wrong job")
            if max(quote["subtotal_cents"], quote["estimated_total_cents"]) > ceiling:
                raise ValueError("Inconsistent amounts")
            if ceiling > int(Decimal(job["input"]["max_total_usd"]) * 100):
                self.change(job, "quote_rejected", "Cart authorization ceiling exceeds the requested USD budget; no payment or checkout")
                return
            if quote["expires_at"] <= self.clock() + 60:
                self.change(job, "quote_expired", "Quote has insufficient validity remaining; no payment or checkout")
                return
            quote.update(job["quote_terms"], purchase_input_hash=digest(job["input"]))
            quote["terms_hash"] = digest(quote)
            job["quote"] = quote
            job["approval_revision"] += 1
            job["input_schema"] = {"input_data": [{"id": "approved", "type": "boolean", "name": "Approve this saved quote",
                "data": {"quote_id": quote["quote_id"], "terms_hash": quote["terms_hash"],
                         "authorization_ceiling_cents": ceiling, "currency": "USD", **job["quote_terms"],
                         "expires_at": quote["expires_at"], "items": quote["items"],
                         "subtotal_cents": quote["subtotal_cents"], "estimated_total_cents": quote["estimated_total_cents"],
                         "revision": job["approval_revision"]}}]}
            job["input_schema_hash"] = digest(job["input_schema"])
            self.change(job, "awaiting_quote_approval", "Quote saved: approve USD ceiling and separate test-ADA price before funding")
        except Exception:
            if job["phase"] != "quote_reconciling":
                self.change(job, "quote_reconciling", "Invalid or ambiguous prepared quote; no payment or checkout")

    async def provide(self, request):
        # Legacy provider approval is a separate state, processed by the original coordinator.
        if self.get(request.job_id)["phase"] != "awaiting_quote_approval":
            return await super().provide(request)
        async with self.lock:
            job = self.get(request.job_id)
            if job["phase"] != "awaiting_quote_approval" or request.input_schema_hash != job["input_schema_hash"]:
                raise Conflict("Quote approval is stale")
            response = request.input_data.model_dump(exclude_none=True)
            if set(response) != {"approved"}:
                raise Conflict("Quote requires an approval decision")
            if job["quote"]["expires_at"] <= self.clock():
                self.change(job, "quote_expired", "Quote expired before approval; no escrow created")
                raise Conflict("Quote expired")
            job["input_schema"] = job["input_schema_hash"] = None
            if not response["approved"]:
                self.change(job, "quote_rejected", "Customer rejected the quote; no escrow or checkout")
                return
            job["approved_quote_hash"] = job["quote"]["terms_hash"]
            self.change(job, "quote_approved", "Customer approved this exact cart, USD ceiling, test-ADA price and payout destination")

    async def create_payment(self, job):
        if self.clock() >= job["quote"]["expires_at"] - 60:
            self.change(job, "quote_expired", "Quote expired before escrow creation; no checkout")
            return
        if job["quote"]["payout_address"] != self.payout_address:
            self.change(job, "manual_review", "Configured payout destination changed since quote approval")
            return
        self.change(job, "creating_payment", "Saved escrow creation attempt for the approved quote")
        try:
            payment = await self.escrow.create(job)
            job["payment"] = payment
            if self.escrow.simulated:
                payment.update(RequestedFunds=job["quote"]["requested_funds"], payoutAddress=self.payout_address)
            if (payment.get("RequestedFunds") != job["quote"]["requested_funds"]
                    or payment.get("payoutAddress") != job["quote"]["payout_address"]):
                self.change(job, "manual_review", "Escrow terms differ from the approved quote; do not fund or purchase")
                return
            self.change(job, "awaiting_payment", "Approved escrow request saved; checkout waits for matching funds locked")
        except Exception:
            self.change(job, "payment_creation_unknown", "Escrow creation needs reconciliation; never create another payment automatically")

    async def tick(self):
        async with self.lock:
            for job in self.store.jobs():
                if job.get("purchase_protocol") != 2:
                    # An existing legacy job requires its original runner, never reinterpret it.
                    continue
                phase = job["phase"]
                if phase in {"paid", "refunded", "manual_review", "quote_rejected", "quote_expired", "payment_creation_unknown"}:
                    continue
                try:
                    if phase == "quote_queued":
                        await self.prepare(job)
                    elif phase in {"preparing_quote", "quote_reconciling"}:
                        self.accept_quote(job, await self.module.inspect_purchase(job_id=job["id"]))
                    elif phase == "awaiting_quote_approval":
                        if self.clock() >= job["quote"]["expires_at"]:
                            self.change(job, "quote_expired", "Quote expired without approval; no funds requested")
                    elif phase == "quote_approved":
                        await self.create_payment(job)
                    elif phase == "creating_payment":
                        self.change(job, "payment_creation_unknown", "Recovered an uncertain escrow creation; inspect before proceeding")
                    elif job["payment"]:
                        if (job["payment"].get("payoutAddress") != job["quote"]["payout_address"]
                                or job["payment"].get("RequestedFunds") != job["quote"]["requested_funds"]):
                            self.change(job, "manual_review", "Saved escrow terms differ from the approved quote")
                            continue
                        await self.advance(job)
                except Exception:
                    self.store.save_job(job, "Provider unavailable; retain saved state and reconcile without repeating writes")

    def public(self, job):
        view = super().public(job)
        if job["phase"] == "awaiting_quote_approval":
            view["status"] = "awaiting_input"
        elif job["phase"] in {"quote_rejected", "quote_expired"}:
            view["status"] = "failed"
        # The full cart stays private. Public quote exposes only approved payment terms and identity.
        quote = job.get("quote")
        view["quote"] = ({k: quote[k] for k in ("quote_id", "currency", "authorization_ceiling_cents", "expires_at",
                           "requested_funds", "payout_address", "terms_hash", "merchant", "items", "subtotal_cents", "estimated_total_cents")} if quote else None)
        view["payment"] = job["payment"] if job["phase"] == "awaiting_payment" else None
        return view

    def evidence(self, job):
        result = super().evidence(job)
        result["quote"] = self.public(job)["quote"]
        result["approved_quote_hash"] = job.get("approved_quote_hash")
        result["checkout_evidence"] = self.store.get("staged_purchase_evidence", job["id"])
        result["confirmation_attempt"] = self.store.get("staged_confirmations", job["id"])
        return result
