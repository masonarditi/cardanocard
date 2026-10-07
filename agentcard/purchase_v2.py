"""Purchase interface v2 for Ezra's staged coordinator (masumi/docs/INTEGRATION_PLAN.md, "Mason v2 handoff").

prepare_purchase quotes without spending, confirm_purchase places the saved quote with the vaulted card once escrow
is funded, inspect_purchase only reads. Every job is saved before any Agentcard call and every confirm attempt before
it is sent, so an uncertain confirm is inspected, never repeated. Responses match masumi's
staged_purchase.PreparedQuote / CheckoutOutcome exactly.
"""
import hashlib
import json
import os
import time
from decimal import Decimal

from agentcard import HERE, PROD, TOKENS, buy, conversation
from purchase import NO_QUESTIONS

PURCHASE_PROTOCOL_VERSION = 2
CARD_LIMIT_CENTS = 5000
QUOTE_TTL_S = 1800
LEDGER = HERE / (".purchase_jobs.prod.json" if PROD else ".purchase_jobs.json")
# Agentcard keeps one Amazon cart per user, so items from earlier conversations can still be in it.
EMPTY_CART = "Start from an empty cart: remove anything already in it, then add only what this message asks for."
FINAL = {"confirmed", "failed_no_purchase", "partial"}
IDENTITY = {"payment_source": "vault", "currency": "USD"}


def _load():
    return json.loads(LEDGER.read_text()) if LEDGER.exists() else {}


# Agentcard's order-ledger vocabulary is only partly observed (v1's gum order showed `placed`; tests assumed
# `settled`). Anything that says the order exists and the charge stuck counts as confirmed; anything that says
# the order died is checked against charge_status; everything else keeps the job pending (never refunded).
ORDER_DONE = {"settled", "placed", "confirmed", "completed", "complete", "shipped", "delivered"}
ORDER_DEAD = {"failed", "cancelled", "canceled", "rejected", "voided"}
CHARGED = {"captured", "authorized", "settled", "charged"}


def _cents(*candidates):
    """First candidate that is a whole number of cents (int, int-valued float or digit string)."""
    for value in candidates:
        if isinstance(value, bool):
            continue
        if isinstance(value, int):
            return value
        if isinstance(value, float) and value.is_integer():
            return int(value)
        if isinstance(value, str) and value.isdigit():
            return int(value)
    return None


def _save(rec):
    tmp = LEDGER.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps({**_load(), rec["job_id"]: rec}, indent=2))
    os.replace(tmp, LEDGER)


def _record(job_id):
    rec = _load().get(job_id)
    if rec is None:
        raise ValueError(f"unknown job_id {job_id}")
    return rec


def _wire(rec):
    """Sanitized response: PreparedQuote until checkout is requested, then CheckoutOutcome."""
    o, q, order = rec["outcome"], rec["quote"] or {}, rec["order"] or {}
    items = [{"name": i["name"], "quantity": i["quantity"]} for i in q.get("items", [])]
    if q and not rec["confirm_called"] and (o["status"] == "prepared" or o.get("reason") == "over_budget"):
        # Over-budget carts are still quoted: the coordinator rejects them by comparing the ceiling to its budget.
        return {"status": "prepared", "job_id": rec["job_id"], "quote_id": q["quote_id"],
                "conversation_id": rec["conversation_id"], "cart_hash": q["cart_hash"], **IDENTITY,
                "subtotal_cents": q["subtotal_cents"], "estimated_total_cents": q["estimated_total_cents"],
                "authorization_ceiling_cents": q["ceiling_cents"], "expires_at": q["expires_at"],
                "merchant": q["merchant"], "items": items}
    out = {"status": o["status"], "job_id": rec["job_id"], "quote_id": q.get("quote_id", ""),
           "conversation_id": rec["conversation_id"] or "", "cart_hash": q.get("cart_hash", ""), **IDENTITY,
           "order_id": order.get("order_id"), "reason": o.get("decline_code") or o.get("reason")}
    if o["status"] == "confirmed":
        # The charge is capped at the authorization ceiling, so the quote's estimate is the fallback total.
        total = _cents(order.get("total_cents"), order.get("totalCents"), o.get("charge_total_cents"),
                       q.get("estimated_total_cents"), q.get("subtotal_cents"))
        out.update(merchant_confirmed=True, charge_status="captured", total_cents=total,
                   merchant=order.get("merchant_name") or q.get("merchant"), items=items)
    elif o["status"] == "failed_no_purchase":
        # Only emitted once Agentcard said nothing was charged (or nothing was ever sent).
        out.update(no_purchase=True, charge_status="none")
    elif o["status"] == "approval_required":
        out["approval_url"] = o.get("approval_url")
    return out


def _out(rec, status, **extra):
    rec["outcome"] = {"status": status, **extra}
    _save(rec)
    return _wire(rec)


def _detail(r):
    return r.get("error") or r.get("reply") if isinstance(r, dict) else r


def _quote(rec, cart, allowed):
    """Saves the cart as the job's quote; returns the reason it can't be confirmed, if any."""
    total, estimate, ceiling = (cart.get(k) for k in ("totalCents", "estimatedTotalCents", "approvedCeilingCents"))
    currency = str(cart.get("merchant_currency") or cart.get("currency") or "usd").lower()
    rec["quote"] = {
        "quote_id": "q_" + hashlib.sha256(f"{rec['job_id']}:{cart['hash']}".encode()).hexdigest()[:16],
        "cart_hash": cart["hash"], "merchant": cart.get("merchant_name") or cart.get("merchant"),
        "items": [{"name": i.get("name"), "quantity": i.get("qty", 1), "price_cents": i.get("priceCents"),
                   "product_id": i.get("product_id")} for i in cart.get("items", [])],
        "currency": currency, "subtotal_cents": total, "estimated_total_cents": total if estimate is None else estimate,
        "ceiling_cents": total if ceiling is None else ceiling, "total_is_estimate": cart.get("totalIsEstimate"),
        "budget_cents": rec["budget_cents"], "expires_at": int(time.time()) + QUOTE_TTL_S}
    # The turn's catalog lists one search only, so a lone item may come from another search; the buyer approves it by name.
    items = cart.get("items") or []
    if not items or any(not i.get("product_id") for i in items) or (
            len(items) > 1 and any(i["product_id"] not in allowed for i in items)):
        return "unexpected_items"
    amounts = [a for a in (total, estimate, ceiling) if a is not None]
    if (total is None or any(type(a) is not int or a < 0 for a in amounts)
            or (cart.get("totalIsEstimate", False) is not False and ceiling is None)):
        return "ambiguous_amount"
    if currency != "usd":
        return "unsupported_currency"
    if max(amounts) > CARD_LIMIT_CENTS:
        return "over_card_limit"  # not quoted: no budget can approve it, so don't let escrow be funded for it
    if max(amounts) > rec["budget_cents"]:
        return "over_budget"


def prepare_purchase(*, job_id: str, intent: str, max_total_usd, address: dict) -> dict:
    """Builds and saves a quote. Never confirms checkout. Repeating a job_id returns its saved state."""
    budget = min(int(Decimal(str(max_total_usd)) * 100), CARD_LIMIT_CENTS)
    fingerprint = hashlib.sha256(json.dumps([intent, budget, address], sort_keys=True).encode()).hexdigest()
    saved = _load().get(job_id)
    if saved:
        if saved["fingerprint"] != fingerprint:
            raise ValueError(f"job_id {job_id} was already used with different inputs")
        return inspect_purchase(job_id=job_id)
    rec = {"job_id": job_id, "env": "prod" if PROD else "sandbox", "fingerprint": fingerprint, "budget_cents": budget,
           "conversation_id": None, "quote": None, "confirm_called": False, "attempts": [], "order": None,
           "outcome": None}
    _save(rec)
    try:
        code, r = buy({"ask": f"{EMPTY_CART} {intent}. {NO_QUESTIONS}", "delivery_address": address})
    except Exception as e:
        return _out(rec, "failed_no_purchase", reason="error", detail=repr(e))
    rec["conversation_id"] = r.get("conversation_id") if isinstance(r, dict) else None
    if code >= 400:
        return _out(rec, "failed_no_purchase", reason="error", http_status=code, detail=_detail(r))
    cart = r.get("cart")
    if len(r.get("carts") or []) > 1:
        return _out(rec, "failed_no_purchase", reason="multiple_carts")
    if not cart or not cart.get("hash"):
        if r.get("status") == "needs_input":
            return _out(rec, "needs_input", reason="needs_input", detail=r.get("reply"))
        return _out(rec, "failed_no_purchase", reason="no_cart", detail=r.get("reply"))
    problem = _quote(rec, cart, {i.get("id") for i in (r.get("catalog") or {}).get("items") or []} - {None})
    return _out(rec, "failed_no_purchase", reason=problem) if problem else _out(rec, "prepared")


def confirm_purchase(*, job_id: str, quote_id: str) -> dict:
    """Places the saved quote with the vaulted card. Call only after escrow is funded and the quote approved."""
    rec = _record(job_id)
    if rec["quote"] and quote_id != rec["quote"]["quote_id"]:
        raise ValueError(f"quote_id {quote_id} is not job {job_id}'s saved quote")
    rec["confirm_called"] = True
    if (rec["order"] or any(a["state"] in ("sent", "charged") for a in rec["attempts"])
            or (rec["outcome"] or {}).get("status") not in ("prepared", "approval_required")):
        _save(rec)
        return inspect_purchase(job_id=job_id)
    attempt = {"quote_id": quote_id, "cart_hash": rec["quote"]["cart_hash"], "sent_at": time.time(), "state": "sent"}
    rec["attempts"].append(attempt)
    _save(rec)
    try:
        code, r = buy({"conversation_id": rec["conversation_id"], "confirm": attempt["cart_hash"],
                       "payment_source": "vault"})
    except Exception as e:
        return _inspect(rec, detail=repr(e))
    if code == 409 and isinstance(r, dict) and r.get("cart"):
        attempt["state"] = "no_charge"
        return _out(rec, "failed_no_purchase", reason="cart_changed", detail=_detail(r))
    if code >= 400:
        # After a confirm no HTTP error proves nothing was charged (408/429/proxy errors included): read the ledger.
        return _inspect(rec, detail=_detail(r))
    decline = r.get("decline_code")
    if r.get("status") == "partially_placed":
        attempt["state"] = "charged"
        rec["order"] = {"order_id": r.get("order_id"), "placements": r.get("placements")}
        return _out(rec, "partial", reason="partially_placed")
    if r.get("status") == "order_placed" or r.get("order_id"):
        attempt["state"] = "charged"
        rec["order"] = {"order_id": r.get("order_id")}
        return _inspect(rec)
    if decline == "vault_approval_required":
        attempt["state"] = "approval_pending"
        return _out(rec, "approval_required", reason=decline, approval_url=r.get("approval_url"))
    if r.get("charge_status") == "none" and decline and decline != "in_progress":
        attempt["state"] = "no_charge"
        return _out(rec, "failed_no_purchase", reason="declined", decline_code=decline)
    return _inspect(rec)


def inspect_purchase(*, job_id: str) -> dict:
    """Read-only: returns the saved state, or reads the Agentcard conversation after a confirm attempt."""
    rec = _record(job_id)
    if rec["outcome"] is None:
        return _out(rec, "failed_no_purchase", reason="prepare_interrupted")
    if rec["outcome"]["status"] in FINAL or not any(a["state"] != "no_charge" for a in rec["attempts"]):
        return _wire(rec)
    return _inspect(rec)


def _inspect(rec, detail=None):
    try:
        conv = conversation(rec["conversation_id"])
    except Exception as e:
        return _out(rec, "pending", reason="inspect_failed", detail=repr(e))
    if conv.get("turn_in_progress") is not False:
        return _out(rec, "pending", reason="in_progress", detail=detail)
    orders, saved_id = conv.get("orders") or [], (rec["order"] or {}).get("order_id")
    if len(orders) > 1:
        rec["order"] = {"order_id": saved_id, "orders": orders}
        return _out(rec, "partial", reason="multiple_orders")
    if orders:
        o = orders[0]
        if saved_id and o.get("order_id") != saved_id:
            return _out(rec, "pending", reason="order_mismatch", detail=o.get("order_id"))
        rec["order"] = {k: o.get(k) for k in ("order_id", "status", "total_cents", "totalCents", "merchant_name",
                                               "placed_at", "charge_status")}
        for a in rec["attempts"]:
            a["state"] = "charged" if a["state"] == "sent" else a["state"]
        status, last = str(o.get("status") or "").lower(), conv.get("last_checkout") or {}
        charge = str(o.get("charge_status") or last.get("charge_status") or "").lower()
        if status in ORDER_DEAD:
            if charge == "none":
                return _out(rec, "failed_no_purchase", reason="funding_failed")
            return _out(rec, "pending", reason=f"order_{status}_charge_{charge or 'unknown'}")
        if status in ORDER_DONE or charge in CHARGED:
            return _out(rec, "confirmed", charge_total_cents=_cents(last.get("total_cents"), last.get("totalCents")))
        return _out(rec, "pending", reason="charge_confirming" if status == "confirming" else f"order_{status or 'unknown'}")
    last = conv.get("last_checkout") or {}
    if saved_id:
        return _out(rec, "pending", reason="order_not_in_ledger_yet")
    if last.get("status") == "needs_approval" or last.get("decline_code") == "vault_approval_required":
        return _out(rec, "approval_required", reason="vault_approval_required", approval_url=last.get("approval_url"))
    if last.get("charge_status") == "none" and last.get("status") in ("denied", "error"):
        for a in rec["attempts"]:
            a["state"] = "no_charge"
        return _out(rec, "failed_no_purchase", reason="declined", decline_code=last.get("decline_code") or last.get("code"),
                    detail=last.get("message"))
    return _out(rec, "pending", reason="unresolved", detail=detail)


def whoami() -> dict:
    """Which Agentcard environment and user this adapter will spend from. Offline; no token refresh."""
    tokens = json.loads(TOKENS.read_text()) if TOKENS.exists() else {}
    return {"protocol": PURCHASE_PROTOCOL_VERSION, "env": "prod" if PROD else "sandbox",
            "user_id": tokens.get("user_id"), "ledger": LEDGER.name, "card_limit_cents": CARD_LIMIT_CENTS}
