"""Single-process job coordinator with durable checkpoints before every side effect."""
import asyncio
import time
import uuid

from pydantic import TypeAdapter

from .models import Outcome, ProvideInput, StartRequest, canonical, digest, parse_purchase_input
from .providers import Escrow, FakeEscrow, Purchaser
from .store import Store


class Conflict(ValueError):
    pass


class Engine:
    def __init__(self, store: Store, escrow: Escrow, purchaser: Purchaser, clock=time.time):
        self.store, self.escrow, self.purchaser, self.clock = store, escrow, purchaser, clock
        self.lock = asyncio.Lock()

    def change(self, job, phase, message):
        job["phase"] = phase
        self.store.save_job(job, message)

    async def start(self, request: StartRequest):
        async with self.lock:
            inputs = parse_purchase_input(request.input_data).model_dump(mode="json")
            # A single configured caller/cardholder for this MVP. Add owner scoping before multi-tenancy.
            existing = next((j for j in self.store.jobs() if j["caller_id"] == request.identifier_from_purchaser), None)
            if existing:
                if digest(existing["wire_input"]) != digest(request.input_data):
                    raise Conflict("Request ID already belongs to different purchase inputs")
                return existing
            job = {"id": str(uuid.uuid4()), "caller_id": request.identifier_from_purchaser,
                   "input": inputs, "wire_input": request.input_data, "phase": "creating_payment", "payment": None,
                   "escrow_state": "AwaitingPayment", "outcome": None, "result": None,
                   "purchase_started": False, "response": None, "input_schema": None,
                   "input_schema_hash": None, "approval_revision": 0,
                   "simulated_escrow": self.escrow.simulated, "simulated_purchase": self.purchaser.simulated}
            self.store.save_job(job, "Reserved job before creating payment request")
            try:
                job["payment"] = await self.escrow.create(job)
            except Exception:
                self.change(job, "payment_creation_unknown", "Payment request outcome requires reconciliation; do not create another")
                return job
            self.change(job, "awaiting_payment", "Payment request saved; purchasing remains disabled until funded")
            return job

    def get(self, job_id):
        job = self.store.get("jobs", job_id)
        if not job:
            raise KeyError(job_id)
        return job

    def public(self, job):
        phase = job["phase"]
        status = ("awaiting_payment" if phase == "awaiting_payment" else
                  "awaiting_input" if phase == "awaiting_input" else
                  "completed" if phase in {"result_submitted", "paid"} else
                  "failed" if phase in {"refund_due", "refund_authorizing", "refunded", "expired"} else "running")
        return {"id": job["id"], "status": status, "phase": phase,
                "escrow_state": job["escrow_state"], "result": job["result"],
                "purchase": job["outcome"], "input_schema": job["input_schema"],
                "input_schema_hash": job["input_schema_hash"], "settlement": self.settlement(job),
                "simulated_escrow": job["simulated_escrow"], "simulated_purchase": job["simulated_purchase"],
                "events": self.store.events(job["id"])}

    def settlement(self, job):
        payment = job.get("payment") or {}
        unlock = payment.get("unlockTime")
        phase = job["phase"]
        status = ("simulated" if job["simulated_escrow"] else
                  "node_reported" if phase in {"paid", "refunded"} else
                  "awaiting_release" if phase == "result_submitted" else "awaiting_order")
        return {"status": status, "payout_address": payment.get("payoutAddress"),
                "unlock_time": unlock, "seconds_until_unlock": max(0, int(unlock - self.clock())) if unlock else None,
                "verified_on_chain": False,
                "note": "Node status is not independent settlement proof; use the acceptance evidence verifier."}

    def evidence(self, job):
        """Explicit proof boundaries, excluding delivery addresses, tokens and approval URLs."""
        purchase = job["outcome"] or {}
        payment = job["payment"] or {}
        return {"job_id": job["id"], "phase": job["phase"],
                "escrow_mode": "simulated" if job["simulated_escrow"] else "preprod",
                "purchase_mode": "simulated" if job["simulated_purchase"] else "external",
                "blockchain_identifier": payment.get("blockchainIdentifier"),
                "chain_transactions": job.get("chain_transactions", []),
                "order_id": purchase.get("order_id"), "merchant": purchase.get("merchant"),
                "total_usd": purchase.get("total_usd"), "escrow_state": job["escrow_state"],
                "result_hash": job.get("result_hash"), "node_action": job.get("node_action"),
                "settlement": self.settlement(job),
                "result": job["result"], "events": self.store.events(job["id"]),
                "note": "A blockchain identifier is not a transaction hash. No chain proof is claimed when transactions are empty."}

    async def provide(self, request: ProvideInput):
        async with self.lock:
            job = self.get(request.job_id)
            if job["phase"] != "awaiting_input" or request.input_schema_hash != job["input_schema_hash"]:
                raise Conflict("Approval is stale or the job is not awaiting input")
            field = job["input_schema"]["input_data"][0]["id"]
            response = request.input_data.model_dump(exclude_none=True)
            if set(response) != {field}:
                raise Conflict("Response does not match the requested input")
            job["response"] = response
            job["input_schema"] = job["input_schema_hash"] = None
            self.change(job, "resume_ready", "Response saved for the same purchase; original inputs stay immutable")

    async def simulate(self, job_id, action):
        async with self.lock:
            if not isinstance(self.escrow, FakeEscrow):
                raise Conflict("Simulation actions are disabled with real escrow")
            job = self.get(job_id)
            if not job.get("payment") or (action == "fund" and job["phase"] != "awaiting_payment"):
                raise Conflict("No approved payment request is ready for this action")
            self.escrow.action(job, action)

    async def tick(self):
        async with self.lock:
            for job in self.store.jobs():
                if job["phase"] in {"paid", "refunded", "manual_review"} or not job["payment"]:
                    continue
                try:
                    await self.advance(job)
                except Exception:
                    # Never convert infrastructure exceptions into a definitive purchase failure.
                    self.store.save_job(job, "Provider unavailable; retained current state for reconciliation")

    async def advance(self, job):
        state = await self.escrow.observe(job)
        job["escrow_state"] = state
        self.store.put("jobs", job["id"], job)
        phase = job["phase"]
        if state in {"ResultSubmitted", "Withdrawn"}:
            if not job["result"]:
                self.change(job, "manual_review", "Escrow result exists without local order evidence")
            else:
                target = "paid" if state == "Withdrawn" else "result_submitted"
                if phase != target:
                    self.change(job, target, "Observed escrow " + state)
            return
        if state == "RefundWithdrawn":
            if job["purchase_started"] and (not job["outcome"] or job["outcome"]["status"] != "failed"):
                self.change(job, "manual_review", "Service fee refunded while merchant outcome needs reconciliation")
            elif phase != "refunded":
                self.change(job, "refunded", "Observed service-fee refund; merchant refunds are separate")
            return
        if state == "RefundRequested":
            definite_failure = job["outcome"] and job["outcome"]["status"] == "failed"
            if definite_failure or not job["purchase_started"]:
                if phase != "refund_authorizing":
                    self.change(job, "refund_authorizing", "Buyer requested refund; authorizing service-fee refund")
                    await self.escrow.authorize_refund(job)
            elif phase != "manual_review":
                self.change(job, "manual_review", "Refund request overlaps a possible merchant purchase; reconcile first")
            return
        if state in {"FundsOrDatumInvalid", "Disputed", "DisputedWithdrawn"}:
            self.change(job, "manual_review", "Escrow requires operator review; no checkout will run")
            return
        if phase == "awaiting_payment" and self.clock() >= job["payment"]["payByTime"] and state != "FundsLocked":
            self.change(job, "expired", "Payment window expired without confirmed funds")
            return
        if state != "FundsLocked":
            return
        if phase == "expired":
            job["outcome"] = {"status": "failed", "reason": "error"}
            self.change(job, "refund_due", "Late funding detected after expiry; do not begin checkout")
            return
        # Recovered/uncertain calls may only inspect. Never restart checkout after a crash.
        if phase in {"purchasing", "reconciling", "processing"}:
            outcome = await self.purchaser.inspect(job["id"])
            await self.accept(job, outcome)
            return
        # Submit-result and refund authorization requests might have succeeded before a timeout.
        # Observe them; do not blindly resubmit or replay the merchant purchase.
        if phase in {"submitting_result", "refund_authorizing", "refund_due", "result_submitted"}:
            return
        if self.clock() >= job["payment"]["submitResultTime"] - 60:
            if job["purchase_started"]:
                self.change(job, "manual_review", "Purchase window closed; inspect the existing order before refund decisions")
            else:
                job["outcome"] = {"status": "failed", "reason": "error"}
                self.change(job, "refund_due", "Result deadline too close to begin checkout; buyer should request refund")
            return
        if phase in {"awaiting_payment", "resume_ready"}:
            job["purchase_started"] = True
            response = job["response"]
            job["response"] = None
            self.change(job, "purchasing", "Checkpointed before calling purchase with the stable job ID")
            try:
                result = await self.purchaser.purchase(job["input"], job["id"], response)
            except Exception:
                self.change(job, "reconciling", "Purchase outcome uncertain; only read-only inspection is allowed")
                return
            # Submission errors must leave the confirmed purchase evidence and submission checkpoint intact.
            await self.accept(job, result)

    async def accept(self, job, raw):
        try:
            outcome = TypeAdapter(Outcome).validate_python(raw)
        except Exception:
            self.change(job, "reconciling", "Unrecognized purchase response; do not assume no charge occurred")
            return
        job["outcome"] = outcome.model_dump(mode="json")
        if outcome.status == "failed":
            self.change(job, "refund_due", "Definitive purchase failure; waiting for buyer refund request")
        elif outcome.status == "success":
            from decimal import Decimal
            if outcome.total_usd > Decimal(job["input"]["max_total_usd"]):
                self.change(job, "manual_review", "Confirmed order exceeds approved budget; purchase must not be repeated")
                return
            job["result"] = canonical({"purchase": job["outcome"], "simulated_purchase": job["simulated_purchase"]})
            if self.clock() >= job["payment"]["submitResultTime"]:
                self.change(job, "manual_review", "Order confirmed after result deadline; retain evidence for settlement review")
                return
            self.change(job, "submitting_result", "Saved exact result bytes before submitting result hash")
            await self.escrow.submit(job, job["result"])
        elif outcome.reason in {"approval_required", "needs_input"}:
            job["approval_revision"] += 1
            field = "approved" if outcome.reason == "approval_required" else "answer"
            job["input_schema"] = {"input_data": [{"id": field,
                "type": "boolean" if field == "approved" else "string", "name": outcome.message or outcome.reason,
                "data": {"description": f"Job {job['id']}, input revision {job['approval_revision']}"}}]}
            job["input_schema_hash"] = digest(job["input_schema"])
            self.change(job, "awaiting_input", "Waiting for a response bound to this purchase and input revision")
        else:
            phase = "processing" if outcome.reason == "processing" else "reconciling"
            if job["phase"] != phase:
                self.change(job, phase, "Purchase is pending; continue read-only inspection")
            else:
                self.store.put("jobs", job["id"], job)
