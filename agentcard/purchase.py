import json
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
        LEDGER.write_text(json.dumps({**_ledger(), request_id: rec}, indent=2))


def _order_from_conversation(conversation_id, cart, polls=20):
    for i in range(polls):
        conv = conversation(conversation_id)
        if not conv.get("turn_in_progress"):
            last = conv.get("last_checkout") or {}
            if last.get("status") == "placed" and last.get("order_id"):
                return _success(last["order_id"], cart)
            return _fail("declined", decline_code=last.get("decline_code"), detail=last.get("message"),
                         conversation_id=conversation_id)
        if i < polls - 1:
            time.sleep(10)
    return _pending(conversation_id, detail="confirm still in progress")


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

    rec.update(conversation_id=cid, cart=cart, confirm_sent=True)
    _save(request_id, rec)
    body = {"conversation_id": cid, "confirm": cart["hash"], "payment_source": "vault", "delivery_address": address}
    try:
        code, r = buy(body)
    except requests.Timeout:
        return _order_from_conversation(cid, cart)
    if code == 409 and r.get("cart"):
        return _fail("price_changed", detail=r.get("error"), cart=r.get("cart"), conversation_id=cid)
    if code >= 500:
        return _order_from_conversation(cid, cart)
    if code >= 400:
        return _fail("error", http_status=code, detail=r, conversation_id=cid)
    if r.get("status") in ("order_placed", "partially_placed") or r.get("order_id"):
        return _success(r.get("order_id"), cart)
    decline = r.get("decline_code")
    if decline == "vault_approval_required":
        return _fail("approval_required", approval_url=r.get("approval_url"), hash=cart["hash"], conversation_id=cid)
    if decline == "sandbox_mode":
        return _fail("sandbox_mode", total_usd=cart["totalCents"] / 100, conversation_id=cid)
    if decline and decline != "in_progress":
        return _fail("declined", decline_code=decline, api_status=r.get("status"), reply=r.get("reply"),
                     conversation_id=cid)
    return _order_from_conversation(cid, cart)


def purchase(ask: str, max_total_usd: float, address: dict, request_id: str = None) -> dict:
    if request_id and request_id in _ledger():
        return inspect_purchase(request_id)
    rec = {}
    _save(request_id, rec)
    try:
        out = _purchase(ask, max_total_usd, address, rec, request_id)
    except Exception as e:
        out = (_pending(rec["conversation_id"], detail=repr(e)) if rec.get("confirm_sent")
               else _fail("error", detail=repr(e)))
    rec["outcome"] = out
    _save(request_id, rec)
    return out


def inspect_purchase(request_id: str) -> dict:
    """Read-only: resolves a saved purchase, never starts or confirms one."""
    rec = _ledger().get(request_id)
    if rec is None or not rec.get("confirm_sent"):
        return (rec or {}).get("outcome") or _fail("error", detail="checkout was never confirmed")
    if rec.get("outcome", {}).get("status") in ("success", "failed"):
        return rec["outcome"]
    try:
        out = _order_from_conversation(rec["conversation_id"], rec["cart"], polls=1)
    except Exception as e:
        return _pending(rec["conversation_id"], detail=repr(e))
    rec["outcome"] = out
    _save(request_id, rec)
    return out
