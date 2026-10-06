"""Purchase interface v2: prepare a quote, confirm it only after escrow is funded, inspect any time.

Every job is saved to a local ledger before any Agentcard call, and every confirm attempt is saved
before it is sent. Uncertain confirms are only ever inspected, never repeated.
"""
import hashlib
import json
import os
import time

from agentcard import HERE, PROD, TOKENS, buy, conversation
from purchase import NO_QUESTIONS

VERSION = 2
CARD_LIMIT_CENTS = 5000
LEDGER = HERE / (".purchase_jobs.prod.json" if PROD else ".purchase_jobs.json")
FINAL = {"confirmed", "failed_no_purchase", "partial"}


def _load():
    return json.loads(LEDGER.read_text()) if LEDGER.exists() else {}


def _save(rec):
    tmp = LEDGER.with_suffix(".tmp")
    tmp.write_text(json.dumps({**_load(), rec["job_id"]: rec}, indent=2))
    os.replace(tmp, LEDGER)


def _record(job_id):
    rec = _load().get(job_id)
    if rec is None:
        raise ValueError(f"unknown job_id {job_id}")
    return rec


def _out(rec, status, **extra):
    rec["outcome"] = {"version": VERSION, "status": status, "job_id": rec["job_id"],
                      "conversation_id": rec["conversation_id"], **extra}
    _save(rec)
    return rec["outcome"]


def _detail(r):
    return r.get("error") or r.get("reply") if isinstance(r, dict) else r


def _quote(rec, cart):
    """Saves the cart as the job's current quote; returns the reason it can't be confirmed, if any."""
    total, estimate, ceiling = (cart.get(k) for k in ("totalCents", "estimatedTotalCents", "approvedCeilingCents"))
    currency = str(cart.get("merchant_currency") or cart.get("currency") or "usd").lower()
    rec["quote"] = {
        "quote_id": "q_" + hashlib.sha256(f"{rec['job_id']}:{cart['hash']}".encode()).hexdigest()[:16],
        "cart_hash": cart["hash"], "merchant": cart.get("merchant_name") or cart.get("merchant"),
        "items": [{"name": i.get("name"), "qty": i.get("qty", 1), "price_cents": i.get("priceCents")}
                  for i in cart.get("items", [])],
        "currency": currency, "subtotal_cents": total, "estimated_total_cents": estimate, "ceiling_cents": ceiling,
        "total_is_estimate": cart.get("totalIsEstimate"), "budget_cents": rec["budget_cents"]}
    amounts = [a for a in (total, estimate, ceiling) if a is not None]
    if (total is None or any(type(a) is not int or a < 0 for a in amounts)
            or (cart.get("totalIsEstimate", False) is not False and ceiling is None)):
        return "ambiguous_amount"
    if currency != "usd":
        return "unsupported_currency"
    if max(amounts) > rec["budget_cents"]:
        return "over_budget"


def _quoted(rec, cart, status="prepared", **extra):
    problem = _quote(rec, cart)
    if problem:
        return _out(rec, "failed_no_purchase", reason=problem, quote=rec["quote"])
    return _out(rec, status, quote=rec["quote"], **extra)


def prepare_purchase(job_id: str, intent: str, max_total_usd, address: dict) -> dict:
    """Builds and saves a quote. Never confirms checkout. Repeating a job_id returns its saved state."""
    budget = min(round(float(max_total_usd) * 100), CARD_LIMIT_CENTS)
    fingerprint = hashlib.sha256(json.dumps([intent, budget, address], sort_keys=True).encode()).hexdigest()
    saved = _load().get(job_id)
    if saved:
        if saved["fingerprint"] != fingerprint:
            raise ValueError(f"job_id {job_id} was already used with different inputs")
        return inspect_purchase(job_id)
    rec = {"job_id": job_id, "env": "prod" if PROD else "sandbox", "fingerprint": fingerprint, "budget_cents": budget,
           "conversation_id": None, "quote": None, "attempts": [], "order": None, "outcome": None}
    _save(rec)
    try:
        code, r = buy({"ask": f"{intent}. {NO_QUESTIONS}", "delivery_address": address})
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
            return _out(rec, "needs_input", reason="needs_input", message=r.get("reply"))
        return _out(rec, "failed_no_purchase", reason="no_cart", detail=r.get("reply"))
    return _quoted(rec, cart)


def confirm_purchase(job_id: str, quote_id: str) -> dict:
    """Places the saved quote with the vaulted card. Call only after escrow is funded and the quote approved."""
    rec = _record(job_id)
    if rec["order"] or any(a["state"] in ("sent", "charged") for a in rec["attempts"]):
        return inspect_purchase(job_id)
    if (rec["outcome"] or {}).get("status") not in ("prepared", "approval_required"):
        return rec["outcome"] or inspect_purchase(job_id)
    if quote_id != rec["quote"]["quote_id"]:
        return _out(rec, "approval_required", reason="quote_changed", quote=rec["quote"])
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
        return _quoted(rec, r["cart"], "approval_required", reason="cart_changed")
    if code >= 500 or code == 409:
        return _inspect(rec, detail=_detail(r))
    if code >= 400:
        attempt["state"] = "no_charge"
        return _out(rec, "failed_no_purchase", reason="error", http_status=code, detail=_detail(r))
    decline = r.get("decline_code")
    if r.get("status") == "partially_placed":
        attempt["state"] = "charged"
        rec["order"] = {"order_id": r.get("order_id"), "placements": r.get("placements")}
        return _out(rec, "partial", order=rec["order"], quote=rec["quote"])
    if r.get("status") == "order_placed" or r.get("order_id"):
        attempt["state"] = "charged"
        rec["order"] = {"order_id": r.get("order_id")}
        return _inspect(rec)
    if decline == "vault_approval_required":
        attempt["state"] = "approval_pending"
        return _out(rec, "approval_required", reason="vault_approval_required", approval_url=r.get("approval_url"),
                    quote=rec["quote"])
    if r.get("charge_status") == "none" and decline and decline != "in_progress":
        attempt["state"] = "no_charge"
        return _out(rec, "failed_no_purchase", reason="declined", decline_code=decline, detail=r.get("reply"))
    return _inspect(rec)


def inspect_purchase(job_id: str) -> dict:
    """Read-only: returns the saved outcome, or reads the Agentcard conversation after a confirm attempt."""
    rec = _record(job_id)
    if rec["outcome"] is None:
        return _out(rec, "failed_no_purchase", reason="prepare_interrupted")
    if rec["outcome"]["status"] in FINAL or not any(a["state"] != "no_charge" for a in rec["attempts"]):
        return rec["outcome"]
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
        return _out(rec, "partial", order=rec["order"], quote=rec["quote"])
    if orders:
        o = orders[0]
        if saved_id and o.get("order_id") != saved_id:
            return _out(rec, "pending", reason="order_mismatch", detail=o.get("order_id"))
        rec["order"] = {k: o.get(k) for k in ("order_id", "status", "total_cents", "merchant_name", "placed_at")}
        for a in rec["attempts"]:
            a["state"] = "charged" if a["state"] == "sent" else a["state"]
        status = o.get("status")
        if status == "settled":
            return _out(rec, "confirmed", order=rec["order"], quote=rec["quote"])
        if status == "failed":
            return _out(rec, "failed_no_purchase", reason="funding_failed", order=rec["order"])
        return _out(rec, "pending", reason="charge_confirming" if status == "confirming" else f"order_{status}",
                    order=rec["order"])
    last = conv.get("last_checkout") or {}
    if saved_id:
        return _out(rec, "pending", reason="order_not_in_ledger_yet", order=rec["order"])
    if last.get("status") == "needs_approval" or last.get("decline_code") == "vault_approval_required":
        return _out(rec, "approval_required", reason="vault_approval_required", approval_url=last.get("approval_url"),
                    quote=rec["quote"])
    if last.get("charge_status") == "none" and last.get("status") in ("denied", "error"):
        for a in rec["attempts"]:
            a["state"] = "no_charge"
        return _out(rec, "failed_no_purchase", reason="declined", decline_code=last.get("decline_code") or last.get("code"),
                    detail=last.get("message"))
    return _out(rec, "pending", reason="unresolved", detail=detail)


def whoami() -> dict:
    """Which Agentcard environment and user this adapter will spend from. Offline; no token refresh."""
    tokens = json.loads(TOKENS.read_text()) if TOKENS.exists() else {}
    return {"version": VERSION, "env": "prod" if PROD else "sandbox", "user_id": tokens.get("user_id"),
            "ledger": LEDGER.name, "card_limit_cents": CARD_LIMIT_CENTS}
