"""Durable checkout around Mason's API client, with a network-free replay transport.

Every write is checkpointed before I/O. Uncertain writes are inspected, never
replayed. This is single-process deduplication, not provider-wide exactly-once.
"""
import asyncio
import importlib.util
from decimal import Decimal

from pydantic import TypeAdapter

from .models import Outcome, PurchaseInput, digest


def pending(reason="unknown", **fields):
    return {"status": "pending", "reason": reason, **fields}


class AgentCardPurchaser:
    def __init__(self, store, transport):
        self.store, self.transport = store, transport
        self.simulated = transport.simulated
        self.lock = asyncio.Lock()

    def save(self, record, outcome=None):
        if outcome is not None:
            record["outcome"] = TypeAdapter(Outcome).validate_python(outcome).model_dump(mode="json")
        self.store.put("agentcard_purchases", record["id"], record)
        return record["outcome"]

    def waiting(self, record, message):
        return self.save(record, pending(conversation_id=record.get("conversation_id"), message=message))

    def cart_limit_issue(self, record, cart):
        amounts = [cart.get("totalCents")]
        for key in ("approvedCeilingCents", "estimatedTotalCents"):
            if cart.get(key) is not None:
                amounts.append(cart[key])
        if (any(type(amount) is not int or amount < 0 for amount in amounts) or
                (cart.get("totalIsEstimate") is True and type(cart.get("approvedCeilingCents")) is not int)):
            return "invalid"
        cap = int(min(Decimal(record["inputs"]["max_total_usd"]), Decimal("50")) * 100)
        record["budget_evidence"] = {"total_cents": cart["totalCents"],
            "approved_ceiling_cents": cart.get("approvedCeilingCents"), "cap_cents": cap,
            "currency_verified": cart.get("currency") == "USD"}
        # Reject conservatively before currency validation; this never grants
        # permission to spend an unknown currency or substitutes USD into a cart.
        return "over_budget" if max(amounts) > cap else None

    async def purchase(self, inputs, request_id, response=None):
        async with self.lock:
            normalized = PurchaseInput.model_validate(inputs).model_dump(mode="json")
            record = self.store.get("agentcard_purchases", request_id)
            if record:
                if record["fingerprint"] != digest(normalized):
                    raise ValueError("Purchase ID belongs to different inputs")
                if record["outcome"]["status"] != "pending":
                    return record["outcome"]
                if response is None:
                    return await self._inspect(record)
                # Only an explicit, persisted no-charge pause permits another write.
                if record["stage"] == "approval" and set(response) == {"approved"}:
                    if response["approved"] is False:
                        record["stage"] = "finished"
                        return self.save(record, {"status": "failed", "reason": "cancelled"})
                    if response["approved"] is not True:
                        raise ValueError("Approval must be boolean")
                    return await self._confirm(record)
                if record["stage"] == "needs_input" and set(response) == {"answer"}:
                    return await self._cart(record, response["answer"])
                return await self._inspect(record)
            if response is not None:
                raise ValueError("Cannot resume an unknown purchase")
            record = {"id": request_id, "fingerprint": digest(normalized), "inputs": normalized,
                      "stage": "reserved", "conversation_id": None, "cart": None,
                      "outcome": pending(), "confirm_attempts": 0}
            self.save(record)
            return await self._cart(record, normalized["ask"])

    async def _cart(self, record, ask):
        record["stage"] = "cart_requested"
        self.save(record, pending(message="Cart request in progress"))
        body = {"ask": ask, "delivery_address": record["inputs"]["address"]}
        if record["conversation_id"]:
            body["conversation_id"] = record["conversation_id"]
        try:
            code, data = await self.transport.buy(body, record["id"])
            if code >= 400 or not isinstance(data, dict):
                return self.waiting(record, "Cart request needs inspection")
            cid = data.get("conversation_id")
            if not isinstance(cid, str) or not cid or (record["conversation_id"] and cid != record["conversation_id"]):
                return self.waiting(record, "Conversation identity could not be verified")
            record["conversation_id"] = cid
            if data.get("status") in {"order_placed", "partially_placed"} or data.get("charge_status") in {"settled", "confirming", "unknown"}:
                record["stage"] = "review"
                return self.waiting(record, "Unexpected purchase evidence during cart creation; manual review required")
            cart = data.get("cart")
            if not cart or not cart.get("hash"):
                record["stage"] = "needs_input" if data.get("status") == "needs_input" else "finished"
                return self.save(record, pending("needs_input", conversation_id=cid,
                    message="Clarify the requested item") if record["stage"] == "needs_input" else
                    {"status": "failed", "reason": "no_cart"})
            if not isinstance(cart["hash"], str):
                return self.waiting(record, "Invalid cart; checkout was not confirmed")
            if len(data.get("carts", [cart])) != 1:
                return self.waiting(record, "This demo supports one cart only")
            record["cart"] = cart
            record["stage"] = "cart_review"
            self.save(record)
            issue = self.cart_limit_issue(record, cart)
            if issue == "invalid":
                return self.waiting(record, "Cart total or approval ceiling is invalid; checkout was not confirmed")
            if issue == "over_budget":
                record["stage"] = "finished"
                return self.save(record, {"status": "failed", "reason": "over_budget"})
            # Purchase API examples omit currency; live use requires explicit USD evidence.
            if cart.get("currency") != "USD":
                return self.waiting(record, "USD cart currency must be verified before checkout")
            record["stage"] = "cart_saved"
            self.save(record)
            return await self._confirm(record)
        except Exception:
            return self.waiting(record, "Cart or checkout outcome uncertain; inspect without retrying")

    async def _confirm(self, record):
        record["stage"] = "confirm_requested"
        record["confirm_attempts"] += 1
        self.save(record, pending(message="Checkout confirmation in progress"))
        body = {"conversation_id": record["conversation_id"], "confirm": record["cart"]["hash"],
                "payment_source": "vault", "delivery_address": record["inputs"]["address"]}
        try:
            code, data = await self.transport.buy(body, record["id"])
            if code >= 400:
                # 409 also means turn_in_progress. Never interpret every 409 as no charge.
                return self.waiting(record, "Checkout response needs inspection; no confirm retry")
            return self._checkout(record, data)
        except Exception:
            return self.waiting(record, "Checkout response lost; inspect the saved conversation")

    def _checkout(self, record, data):
        if data.get("conversation_id") not in {None, record["conversation_id"]}:
            return self.waiting(record, "Checkout conversation does not match")
        if data.get("status") == "partially_placed":
            record["stage"] = "review"
            return self.waiting(record, "Partial order requires review; do not refund automatically")
        if data.get("charge_status") in {"unknown", "confirming"} or data.get("decline_code") == "in_progress":
            return self.waiting(record, "Checkout is unresolved")
        if data.get("status") == "order_placed" and data.get("order_id"):
            record["order_id"] = data["order_id"]
            record["stage"] = "order_placed"
            # Retail placement is not merchant acceptance. Track before settling escrow.
            return self.save(record, pending("processing", conversation_id=record["conversation_id"],
                                            message="Order placed; waiting for merchant confirmation"))
        if data.get("charge_status") == "none" and data.get("status") == "declined":
            decline = data.get("decline_code")
            if decline == "vault_approval_required":
                record["stage"] = "approval"
                return self.save(record, pending("approval_required", conversation_id=record["conversation_id"],
                    approval_url=data.get("approval_url"), message="Approve at AgentCard, then continue this cart"))
            if decline in {"sandbox_mode", "items_unavailable", "unsupported_currency", "currency_changed", "no_cart"}:
                record["stage"] = "finished"
                record["provider_reason"] = decline
                return self.save(record, {"status": "failed", "reason": "declined"})
        return self.waiting(record, "No definitive order or no-charge failure evidence")

    async def inspect(self, request_id):
        async with self.lock:
            record = self.store.get("agentcard_purchases", request_id)
            if not record:
                return pending(message="No saved purchase; manual reconciliation required")
            return await self._inspect(record)

    async def _inspect(self, record):
        if record["outcome"]["status"] != "pending" or record["stage"] in {"approval", "needs_input", "review"}:
            return record["outcome"]
        if not record["conversation_id"]:
            return self.waiting(record, "Conversation reference unavailable; manual reconciliation required")
        try:
            if record.get("order_id"):
                evidence = await self.transport.track(record["order_id"], record["id"])
                # Normalized evidence supplied by a separately verified transport mapping.
                # Unknown vendor shapes deliberately stay pending.
                if (evidence.get("merchant_confirmed") is not True or
                    evidence.get("order_id") != record["order_id"] or evidence.get("currency") != "USD" or
                    type(evidence.get("total_cents")) is not int):
                    return self.waiting(record, "Waiting for verified merchant order evidence")
                total = evidence["total_cents"]
                if total < 0 or total > int(min(Decimal(record["inputs"]["max_total_usd"]), Decimal("50")) * 100):
                    record["stage"] = "review"
                    return self.waiting(record, "Final total exceeds authorization or is invalid; manual review required")
                outcome = {"status": "success", "order_id": record["order_id"],
                           "total_usd": str(Decimal(total) / 100), "merchant": evidence["merchant"],
                           "items": evidence["items"]}
                self.save(record, outcome)
                record["stage"] = "finished"
                return self.save(record)
            data = await self.transport.conversation(record["conversation_id"], record["id"])
            if data.get("turn_in_progress") is not False:
                return self.waiting(record, "Conversation is still processing or status is unknown")
            if data.get("id", data.get("conversation_id")) != record["conversation_id"]:
                return self.waiting(record, "Conversation identity could not be verified")
            # A single fresh conversation belongs to this job. Ambiguous/multiple orders need review.
            orders = data.get("orders", [])
            if len(orders) == 1 and isinstance(orders[0], dict) and (orders[0].get("order_id") or orders[0].get("id")):
                record["order_id"] = orders[0].get("order_id") or orders[0]["id"]
                record["stage"] = "order_placed"
                return self.save(record, pending("processing", conversation_id=record["conversation_id"],
                                                message="Recovered order; verifying merchant confirmation"))
            checkout = data.get("last_checkout")
            carts = data.get("carts", [])
            if (orders == [] and checkout is None and record["confirm_attempts"] == 0 and
                    record["stage"] in {"cart_requested", "cart_review"} and
                    isinstance(carts, list) and len(carts) == 1 and isinstance(carts[0], dict)):
                # Recover a validation-paused cart from the same idle conversation.
                # Inspection can reject it, but must never initiate confirmation.
                cart = carts[0]
                saved = record.get("cart")
                if (isinstance(cart.get("hash"), str) and cart["hash"] and
                        (saved is None or saved.get("hash") == cart["hash"])):
                    record["cart"] = cart
                    record["stage"] = "cart_review"
                    if self.cart_limit_issue(record, cart) == "over_budget":
                        record["stage"] = "finished"
                        return self.save(record, {"status": "failed", "reason": "over_budget"})
                    return self.waiting(record, "Cart requires validation; read-only inspection will not confirm it")
            # A fresh conversation is private to this job. Recover only a known
            # confirmation's explicit no-charge decline, never infer one from silence.
            if (not orders and record["stage"] == "confirm_requested" and
                    isinstance(checkout, dict) and checkout.get("status") == "denied" and
                    checkout.get("charge_status") == "none" and not checkout.get("order_id")):
                return self._checkout(record, {**checkout, "status": "declined",
                    "conversation_id": record["conversation_id"]})
            return self.waiting(record, "No definitive order evidence; checkout will not be repeated")
        except Exception:
            return self.waiting(record, "Provider inspection unavailable; retained purchase references")


class ReplayTransport:
    """Synthetic vendor-shaped responses. No credential use or network access."""
    simulated = True
    SCENARIOS = {"success", "declined", "over_budget", "approval", "needs_input", "unknown", "timeout_recovered", "partial"}

    def __init__(self, store, scenario="success"):
        if scenario not in self.SCENARIOS:
            raise ValueError("Unsupported replay scenario")
        self.store, self.scenario = store, scenario

    async def buy(self, body, request_id):
        saved = self.store.get("agentcard_replay", request_id) or {
            "scenario": (self.store.get("demo_scenarios", request_id) or {}).get("scenario", self.scenario),
            "writes": 0, "confirms": 0, "order": False}
        saved["writes"] += 1
        cid = "SIM-CONV-" + request_id
        scenario = saved["scenario"]
        cart = {"hash": "SIM-CART-" + request_id, "totalCents": 750, "currency": "USD",
                "merchant": "Simulated shop", "items": [{"name": "Sample item", "qty": 1}]}
        if scenario == "over_budget":
            cart["totalCents"] = 100000
        if "confirm" not in body:
            self.store.put("agentcard_replay", request_id, saved)
            return 200, {"conversation_id": cid, "status": "needs_input",
                         "cart": None if scenario == "needs_input" and saved["writes"] == 1 else cart}
        saved["confirms"] += 1
        if scenario == "approval" and saved["confirms"] == 1:
            result = {"status": "declined", "decline_code": "vault_approval_required", "charge_status": "none"}
        elif scenario == "declined":
            result = {"status": "declined", "decline_code": "sandbox_mode", "charge_status": "none"}
        elif scenario == "unknown":
            result = {"charge_status": "unknown"}
        elif scenario == "partial":
            result = {"status": "partially_placed", "charge_status": "settled"}
        else:
            saved["order"] = True
            result = {"status": "order_placed", "order_id": "SIM-ORDER-" + request_id, "charge_status": "settled"}
        self.store.put("agentcard_replay", request_id, saved)
        if scenario == "timeout_recovered":
            raise TimeoutError("Synthetic lost confirm response")
        return 200, {"conversation_id": cid, **result}

    async def conversation(self, conversation_id, request_id):
        saved = self.store.get("agentcard_replay", request_id) or {}
        return {"id": conversation_id, "turn_in_progress": saved.get("scenario") == "unknown",
                "orders": [{"id": "SIM-ORDER-" + request_id}] if saved.get("order") else []}

    async def track(self, order_id, request_id):
        return {"merchant_confirmed": True, "order_id": order_id, "currency": "USD", "total_cents": 750,
                "merchant": "Simulated shop", "items": [{"name": "Sample item", "qty": 1}]}


class MasonClientTransport:
    """Dependency-injected Mason client. Not enabled by the application factory.

    A sandbox session/account check and verified order-tracking response mapping
    must be supplied before network checkout is made available to the demo.
    """
    simulated = False

    def __init__(self, client, verify_sandbox, read_confirmed_order, *, exclusive_access=None):
        self.client = client
        self.verify_sandbox = verify_sandbox
        self.read_confirmed_order = read_confirmed_order
        # Manual handoff from Mason is required. A local mutex cannot protect
        # rotating user tokens shared across two machines. Reads can refresh too.
        self.exclusive_access = exclusive_access

    def require_handoff(self):
        if self.exclusive_access is None or self.exclusive_access() is not True:
            raise ValueError("Mason's exclusive sandbox token handoff is required before authenticated calls")

    @classmethod
    def from_path(cls, path, verify_sandbox, read_confirmed_order, *, exclusive_access=None):
        """Load the trusted local agentcard/agentcard.py without package-name collisions."""
        spec = importlib.util.spec_from_file_location("cardano_card_mason_client", path)
        if spec is None or spec.loader is None:
            raise ValueError("Mason client module was not found")
        client = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(client)
        return cls(client, verify_sandbox, read_confirmed_order, exclusive_access=exclusive_access)

    async def buy(self, body, request_id):
        self.require_handoff()
        if await self.verify_sandbox() is not True:
            raise ValueError("Sandbox identity has not been verified")
        return await asyncio.to_thread(self.client.buy, body)

    async def conversation(self, conversation_id, request_id):
        self.require_handoff()
        return await asyncio.to_thread(self.client.conversation, conversation_id)

    async def track(self, order_id, request_id):
        self.require_handoff()
        return await self.read_confirmed_order(order_id)
