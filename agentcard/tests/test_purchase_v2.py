import sys
import time
from pathlib import Path

import pytest
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import purchase_v2 as v2  # noqa: E402

ADDRESS = {"street": "1 Test St", "city": "San Francisco", "state": "CA", "zip": "94123", "phone": "+14155550100",
           "name": "Test Buyer"}
CART = {"hash": "h1", "merchant": "retail", "merchant_name": "Amazon", "serviceFeesCents": 0, "tipCents": 0,
        "items": [{"name": "Trident gum", "qty": 1, "priceCents": 132, "product_id": "B0GUM"}], "totalCents": 132,
        "totalIsEstimate": True, "approvedCeilingCents": 1206}
CATALOG = {"items": [{"id": "B0GUM", "name": "Trident gum", "priceCents": 132}]}
QUOTED = (200, {"conversation_id": "conv_1", "status": "needs_input", "cart": CART, "catalog": CATALOG})
PLACED = (200, {"conversation_id": "conv_1", "status": "order_placed", "order_id": "ord_1", "charge_status": "settled"})
SETTLED = {"turn_in_progress": False, "orders": [{"order_id": "ord_1", "status": "settled", "total_cents": 270,
                                                  "merchant_name": "Amazon", "placed_at": "2026-10-06T10:00:00Z"}]}


class FakeAgentcard:
    def __init__(self):
        self.buys, self.calls, self.convs = [], [], []

    def buy(self, body, timeout=150):
        self.calls.append(body)
        result = self.buys.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result

    def conversation(self, conversation_id):
        return self.convs.pop(0)

    def confirms(self):
        return [c for c in self.calls if "confirm" in c]


@pytest.fixture
def api(tmp_path, monkeypatch):
    fake = FakeAgentcard()
    monkeypatch.setattr(v2, "LEDGER", tmp_path / "jobs.json")
    monkeypatch.setattr(v2, "buy", fake.buy)
    monkeypatch.setattr(v2, "conversation", fake.conversation)
    return fake


def prepare(api, job="job_1", budget="15.00", response=QUOTED):
    api.buys.append(response)
    return v2.prepare_purchase(job_id=job, intent="a pack of Trident gum from Amazon", max_total_usd=budget,
                               address=ADDRESS)


def confirm(quote_id, job="job_1"):
    return v2.confirm_purchase(job_id=job, quote_id=quote_id)


def test_prepare_never_spends(api):
    q = prepare(api)
    assert (q["status"], q["currency"], q["payment_source"]) == ("prepared", "USD", "vault") and not api.confirms()
    assert (q["subtotal_cents"], q["estimated_total_cents"], q["authorization_ceiling_cents"]) == (132, 132, 1206)
    assert q["items"] == [{"name": "Trident gum", "quantity": 1}] and q["expires_at"] > time.time() + 1700
    assert v2.inspect_purchase(job_id="job_1") == q


def test_confirmed_needs_settled_order_not_just_an_order_id(api):
    q = prepare(api)
    api.buys.append(PLACED)
    api.convs += [{"turn_in_progress": False, "orders": [{**SETTLED["orders"][0], "status": "confirming"}]}, SETTLED]
    out = confirm(q["quote_id"])
    assert (out["status"], out["reason"], out["order_id"]) == ("pending", "charge_confirming", "ord_1")
    assert api.confirms() == [{"conversation_id": "conv_1", "confirm": "h1", "payment_source": "vault"}]
    out = v2.inspect_purchase(job_id="job_1")
    assert (out["status"], out["merchant_confirmed"], out["charge_status"]) == ("confirmed", True, "captured")
    assert (out["order_id"], out["total_cents"], out["merchant"]) == ("ord_1", 270, "Amazon")
    assert (out["quote_id"], out["cart_hash"], out["conversation_id"]) == (q["quote_id"], "h1", "conv_1")
    assert confirm(q["quote_id"]) == out and len(api.confirms()) == 1


def test_vault_approval_is_resolved_by_inspect(api):
    q = prepare(api)
    api.buys.append((200, {"status": "declined", "decline_code": "vault_approval_required", "charge_status": "none",
                           "approval_url": "https://vault.agentcard.sh/authorize?id=cauth_1"}))
    out = confirm(q["quote_id"])
    assert out["status"] == "approval_required" and out["approval_url"].startswith("https://")
    api.convs.append(SETTLED)
    assert v2.inspect_purchase(job_id="job_1")["status"] == "confirmed" and len(api.confirms()) == 1


def test_over_budget_is_quoted_for_the_coordinator_but_never_confirmed(api):
    q = prepare(api, response=(200, {**QUOTED[1], "cart": {**CART, "approvedCeilingCents": 2348}}))
    assert (q["status"], q["authorization_ceiling_cents"]) == ("prepared", 2348)
    out = confirm(q["quote_id"])
    assert (out["status"], out["reason"], out["no_purchase"]) == ("failed_no_purchase", "over_budget", True)
    assert v2.inspect_purchase(job_id="job_1") == out and not api.confirms()


@pytest.mark.parametrize("cart,reason", [
    ({**CART, "approvedCeilingCents": None}, "ambiguous_amount"),
    ({**CART, "totalCents": "132"}, "ambiguous_amount"),
    ({**CART, "merchant_currency": "cad"}, "unsupported_currency"),
    ({**CART, "items": CART["items"] + [{"name": "Coffee from an earlier job", "qty": 1, "priceCents": 1211,
                                         "product_id": "B0COFFEE"}]}, "unexpected_items"),
    ({**CART, "items": [{**CART["items"][0], "product_id": None}]}, "unexpected_items"),
])
def test_unconfirmable_carts_fail_without_purchase(api, cart, reason):
    out = prepare(api, response=(200, {**QUOTED[1], "cart": cart}))
    assert (out["status"], out["reason"], out["no_purchase"], out["charge_status"]) == (
        "failed_no_purchase", reason, True, "none")
    assert confirm(out["quote_id"]) == out and not api.confirms()


def test_changed_cart_on_confirm_is_no_purchase(api):
    q = prepare(api)
    api.buys.append((409, {"error": "price_changed", "cart": {**CART, "hash": "h2", "totalCents": 150}}))
    out = confirm(q["quote_id"])
    assert (out["status"], out["reason"], out["no_purchase"]) == ("failed_no_purchase", "cart_changed", True)
    assert out["cart_hash"] == "h1" and confirm(q["quote_id"]) == out and len(api.confirms()) == 1


def test_wrong_quote_id_is_a_caller_error(api):
    prepare(api)
    with pytest.raises(ValueError):
        confirm("q_not_this_one")
    assert not api.confirms()


def test_timeout_is_inspected_never_repeated(api):
    q = prepare(api)
    api.buys.append(requests.Timeout("read timed out"))
    api.convs += [{"turn_in_progress": True}, SETTLED]
    assert confirm(q["quote_id"])["reason"] == "in_progress"
    assert confirm(q["quote_id"])["status"] == "confirmed" and len(api.confirms()) == 1


def test_duplicate_requests(api):
    first = prepare(api)
    assert v2.prepare_purchase(job_id="job_1", intent="a pack of Trident gum from Amazon", max_total_usd="15.00",
                               address=ADDRESS) == first
    assert len(api.calls) == 1
    with pytest.raises(ValueError):
        v2.prepare_purchase(job_id="job_1", intent="a pack of Trident gum from Amazon", max_total_usd="20.00",
                            address=ADDRESS)
    with pytest.raises(ValueError):
        v2.inspect_purchase(job_id="job_unknown")


def test_restart_after_confirm_sent_inspects(api):
    q = prepare(api)
    api.buys.append(SystemExit("process killed mid-confirm"))
    with pytest.raises(SystemExit):
        confirm(q["quote_id"])
    assert v2._load()["job_1"]["attempts"][0]["state"] == "sent"
    api.convs.append(SETTLED)
    assert confirm(q["quote_id"])["status"] == "confirmed" and len(api.confirms()) == 1


def test_restart_during_prepare_is_no_purchase(api):
    api.buys.append(SystemExit("process killed mid-prepare"))
    with pytest.raises(SystemExit):
        prepare(api, response=None)
    assert v2.inspect_purchase(job_id="job_1")["reason"] == "prepare_interrupted"


def test_partial_order_is_not_success(api):
    q = prepare(api)
    api.buys.append((200, {"status": "partially_placed", "order_id": None, "charge_status": "settled",
                           "placements": [{"merchant": "retail", "order_id": "ord_1"}]}))
    out = confirm(q["quote_id"])
    assert out["status"] == "partial" and "no_purchase" not in out and v2.inspect_purchase(job_id="job_1") == out


def test_sandbox_decline_is_no_purchase(api):
    q = prepare(api)
    api.buys.append((200, {"status": "needs_input", "decline_code": "sandbox_mode", "charge_status": "none"}))
    out = confirm(q["quote_id"])
    assert (out["status"], out["reason"], out["no_purchase"]) == ("failed_no_purchase", "sandbox_mode", True)


def test_decline_without_charge_status_is_resolved_from_conversation(api):
    q = prepare(api)
    api.buys.append((200, {"status": "declined", "decline_code": "sandbox_mode"}))
    api.convs.append({"turn_in_progress": False, "orders": [],
                      "last_checkout": {"status": "denied", "decline_code": "sandbox_mode", "charge_status": "none"}})
    assert confirm(q["quote_id"])["status"] == "failed_no_purchase"


def test_server_error_is_pending_not_refundable(api):
    q = prepare(api)
    api.buys.append((502, {"error": "bad gateway"}))
    api.convs.append({"turn_in_progress": False, "orders": [], "last_checkout": None})
    assert confirm(q["quote_id"])["status"] == "pending"


@pytest.mark.parametrize("response,status", [
    ((200, {"conversation_id": "conv_1", "status": "needs_input", "reply": "Which flavor?"}), "needs_input"),
    ((200, {"conversation_id": "conv_1", "status": "done", "reply": "Nothing matched"}), "failed_no_purchase"),
    ((500, {"error": "boom"}), "failed_no_purchase"),
])
def test_prepare_without_cart(api, response, status):
    out = prepare(api, response=response)
    assert out["status"] == status and "Which flavor" not in str(out) and not api.confirms()
