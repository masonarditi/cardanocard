import json
import os
import time

import requests

from agentcard import HERE, buy, conversation

CARD_LIMIT_USD = 50.0
LEDGER = HERE / ".purchases.json"

NO_QUESTIONS = ("If several products match, pick the single best-value one yourself and build the cart now. "
                "Do not ask me any questions; I cannot reply.")


def _fail(reason, **extra):
    return {"status": "failed", "reason": reason, **extra}


def _pending(conversation_id=None, **extra):
    return {"status": "pending", "reason": "unknown", "conversation_id": conversation_id, **extra}


def _success(order_id, cart):
    return {"status": "success", "order_id": order_id, "total_usd": cart["totalCents"] / 100,
            "merchant": cart.get("merchant_name") or cart.get("merchant"),
            "items": [{"name": i["name"], "qty": i.get("qty", 1), "price_usd": i["priceCents"] / 100}
                      for i in cart.get("items", [])]}


def _ledger():
    return json.loads(LEDGER.read_text()) if LEDGER.exists() else {}


def _save(request_id, rec):
    if request_id:
        # Atomic replace: a crash mid-write must never corrupt the ledger.
        tmp = LEDGER.with_suffix(f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps({**_ledger(), request_id: rec}, indent=2))
        os.replace(tmp, LEDGER)


def _order_from_conversation(conversation_id, cart, polls=20):
    for i in range(polls):
        conv = conversation(conversation_id)
        if not conv.get("turn_in_progress"):
            orders = conv.get("orders") or []
            last = conv.get("last_checkout") or {}
            if len(orders) > 1:
                return _pending(conversation_id, detail="several orders placed; reconcile manually")
            if orders and orders[0].get("order_id"):
                if orders[0].get("status") == "failed":
                    if last.get("charge_status") == "none":
                        return _fail("declined", decline_code="funding_failed", conversation_id=conversation_id)
                    return _pending(conversation_id, detail="order failed after placement; check the charge")
                return _success(orders[0]["order_id"], cart)
            if last.get("status") == "placed" and last.get("order_id"):
                return _success(last["order_id"], cart)
            if last.get("charge_status") == "none" and last.get("status") in ("denied", "error"):
                return _fail("declined", decline_code=last.get("decline_code"), detail=last.get("message"),
                             conversation_id=conversation_id)
            # Missing or still-settling checkout: a charge may exist, so never report "failed" here.
            return _pending(conversation_id, detail=f"checkout {last.get('status') or 'missing'}")
        if i < polls - 1:
            time.sleep(10)
    return _pending(conversation_id, detail="confirm still in progress")


APPROVAL_WAIT_S = int(os.environ.get("AGENTCARD_APPROVAL_WAIT_S", "600"))


def _await_approval(cid, cart, body, r, rec, request_id):
    """Agentcard sent the cardholder an approval link (and a push) for this exact cart. Re-confirm the same cart hash
    every 15 s until the cardholder approves, the link expires or the wait budget runs out. The link is kept in the
    ledger record so an operator can forward it."""
    url = r.get("approval_url")
    rec["approval_url"] = url
    _save(request_id, rec)
    deadline = time.time() + APPROVAL_WAIT_S
    while time.time() < deadline:
        time.sleep(15)
        try:
            code, r = buy(body)
        except requests.Timeout:
            return _order_from_conversation(cid, cart)
        if code >= 400:
            return _order_from_conversation(cid, cart)
        if r.get("order_id"):
            return _success(r["order_id"], cart)
        if r.get("status") in ("order_placed", "partially_placed"):
            return _order_from_conversation(cid, cart)
        decline = r.get("decline_code")
        if decline == "vault_approval_required":
            url = r.get("approval_url") or url
            continue
        if decline == "vault_approval_expired":
            break
        if decline and decline != "in_progress" and r.get("charge_status") == "none":
            return _fail("declined", decline_code=decline, approval_url=url, conversation_id=cid)
        return _order_from_conversation(cid, cart)
    return _fail("approval_required", approval_url=url, hash=cart["hash"], conversation_id=cid)


def _duplicate(out, request_id):
    """Agentcard's confirm is idempotent on the cart hash: an identical cart inside its window returns the EARLIER
    order instead of placing a new one. An order id already paid for by another job is therefore not a purchase."""
    if out.get("status") != "success" or not request_id:
        return out
    try:
        for other, rec in _ledger().items():
            if other != request_id and (rec.get("outcome") or {}).get("order_id") == out["order_id"]:
                return _fail("declined", decline_code="duplicate_order", order_id=out["order_id"], first_request_id=other,
                             detail="Agentcard returned an order that another job already paid for; nothing new was ordered")
    except Exception:
        return _pending(None, detail="ledger unavailable for the duplicate-order check")
    return out


def _purchase(ask, max_total_usd, address, rec, request_id):
    code, r = buy({"ask": f"{ask}. {NO_QUESTIONS}", "delivery_address": address})
    if code >= 400:
        return _fail("error", http_status=code, detail=r)
    cart, cid = r.get("cart"), r.get("conversation_id")
    if not cart or not cart.get("hash"):
        return _fail("needs_input" if r.get("status") == "needs_input" else "no_cart",
                     reply=r.get("reply"), unmatched=r.get("unmatched"), conversation_id=cid)
    if cart["totalCents"] > round(min(max_total_usd, CARD_LIMIT_USD) * 100):
        return _fail("over_budget", total_usd=cart["totalCents"] / 100, conversation_id=cid)
    # Agentcard keeps one Amazon cart per user: a leftover from an earlier job would be bought alongside this one.
    # Non-conversational purchases are one item (any quantity); anything else is not the cart that was asked for.
    if len(cart.get("items") or []) != 1:
        return _fail("no_cart", detail="cart does not hold exactly one line item",
                     items=[i.get("name") for i in (cart.get("items") or [])], conversation_id=cid)

    rec.update(conversation_id=cid, cart=cart, confirm_sent=True)
    _save(request_id, rec)
    body = {"conversation_id": cid, "confirm": cart["hash"], "payment_source": "vault", "delivery_address": address}
    try:
        code, r = buy(body)
    except requests.Timeout:
        return _order_from_conversation(cid, cart)
    if code == 409 and r.get("cart"):
        return _fail("price_changed", detail=r.get("error"), cart=r.get("cart"), conversation_id=cid)
    if code >= 400:
        # After a confirm, no HTTP error proves nothing was charged (408, 429, proxy errors...).
        return _order_from_conversation(cid, cart)
    if r.get("status") == "partially_placed":
        return _pending(cid, detail="partially placed; reconcile manually")
    if r.get("order_id"):
        return _success(r["order_id"], cart)
    if r.get("status") == "order_placed":
        return _order_from_conversation(cid, cart)  # fetch the order id
    decline = r.get("decline_code")
    if decline == "vault_approval_required":
        return _await_approval(cid, cart, body, r, rec, request_id)
    if decline == "sandbox_mode":
        return _fail("sandbox_mode", total_usd=cart["totalCents"] / 100, conversation_id=cid)
    if decline and decline != "in_progress" and r.get("charge_status") == "none":
        return _fail("declined", decline_code=decline, api_status=r.get("status"), reply=r.get("reply"),
                     conversation_id=cid)
    return _order_from_conversation(cid, cart)


def purchase(ask: str, max_total_usd: float, address: dict, request_id: str = None) -> dict:
    try:
        if request_id and request_id in _ledger():
            return inspect_purchase(request_id)
        rec = {}
        _save(request_id, rec)
    except Exception as e:
        # Unreadable ledger: we cannot tell whether this request already bought something.
        return _pending(None, detail=f"ledger unavailable: {e!r}")
    try:
        out = _purchase(ask, max_total_usd, address, rec, request_id)
    except Exception as e:
        out = (_pending(rec["conversation_id"], detail=repr(e)) if rec.get("confirm_sent")
               else _fail("error", detail=repr(e)))
    out = _duplicate(out, request_id)
    rec["outcome"] = out
    try:
        _save(request_id, rec)
    except Exception:
        pass
    return out


def inspect_purchase(request_id: str) -> dict:
    """Read-only: resolves a saved purchase, never starts or confirms one."""
    try:
        rec = _ledger().get(request_id)
    except Exception as e:
        return _pending(None, detail=f"ledger unavailable: {e!r}")
    if rec is None:
        # No record on this machine (moved host, deleted file): a charge can't be ruled out.
        return _pending(None, detail="no ledger record for this request on this machine")
    if not rec.get("confirm_sent"):
        return rec.get("outcome") or _fail("error", detail="checkout was never confirmed")
    if rec.get("outcome", {}).get("status") in ("success", "failed"):
        return rec["outcome"]
    try:
        out = _order_from_conversation(rec["conversation_id"], rec["cart"], polls=1)
    except Exception as e:
        return _pending(rec["conversation_id"], detail=repr(e))
    out = _duplicate(out, request_id)
    rec["outcome"] = out
    try:
        _save(request_id, rec)
    except Exception:
        pass
    return out
