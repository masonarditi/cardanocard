import time

import requests

from agentcard import buy, conversation

CARD_LIMIT_USD = 50.0

NO_QUESTIONS = ("If several products match, pick the single best-value one yourself and build the cart now. "
                "Do not ask me any questions; I cannot reply.")


def _fail(reason, **extra):
    return {"status": "failed", "reason": reason, **extra}


def _success(order_id, cart):
    return {"status": "success", "order_id": order_id, "total_usd": cart["totalCents"] / 100,
            "merchant": cart.get("merchant_name") or cart.get("merchant"),
            "items": [{"name": i["name"], "qty": i.get("qty", 1), "price_usd": i["priceCents"] / 100}
                      for i in cart.get("items", [])]}


def _order_from_conversation(conversation_id, cart):
    for _ in range(20):
        conv = conversation(conversation_id)
        if not conv.get("turn_in_progress"):
            last = conv.get("last_checkout") or {}
            if last.get("status") == "placed" and last.get("order_id"):
                return _success(last["order_id"], cart)
            return _fail("declined", decline_code=last.get("decline_code"), detail=last.get("message"),
                         conversation_id=conversation_id)
        time.sleep(10)
    return _fail("error", detail="confirm still in progress", conversation_id=conversation_id)


def _purchase(ask, max_total_usd, address):
    code, r = buy({"ask": f"{ask}. {NO_QUESTIONS}", "delivery_address": address})
    if code >= 400:
        return _fail("error", http_status=code, detail=r)
    cart, cid = r.get("cart"), r.get("conversation_id")
    if not cart or not cart.get("hash"):
        return _fail("needs_input" if r.get("status") == "needs_input" else "no_cart",
                     reply=r.get("reply"), unmatched=r.get("unmatched"), conversation_id=cid)
    if cart["totalCents"] > round(min(max_total_usd, CARD_LIMIT_USD) * 100):
        return _fail("over_budget", total_usd=cart["totalCents"] / 100, conversation_id=cid)

    body = {"conversation_id": cid, "confirm": cart["hash"], "payment_source": "vault", "delivery_address": address}
    try:
        code, r = buy(body)
    except requests.Timeout:
        return _order_from_conversation(cid, cart)
    if code == 409:
        return _fail("price_changed", detail=r.get("error"), cart=r.get("cart"), conversation_id=cid)
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


def purchase(ask: str, max_total_usd: float, address: dict) -> dict:
    try:
        return _purchase(ask, max_total_usd, address)
    except Exception as e:
        return _fail("error", detail=repr(e))
