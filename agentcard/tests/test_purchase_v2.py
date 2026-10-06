import sys
from pathlib import Path

import pytest
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import purchase_v2 as v2  # noqa: E402

ADDRESS = {"street": "1 Test St", "city": "San Francisco", "state": "CA", "zip": "94123", "phone": "+14155550100",
           "name": "Test Buyer"}
CART = {"hash": "h1", "merchant": "retail", "merchant_name": "Amazon", "serviceFeesCents": 0, "tipCents": 0,
        "items": [{"name": "Trident gum", "qty": 1, "priceCents": 132}], "totalCents": 132, "totalIsEstimate": True,
        "approvedCeilingCents": 1206}
QUOTED = (200, {"conversation_id": "conv_1", "status": "needs_input", "cart": CART})
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


def prepare(api, job="job_1", budget=15, response=QUOTED):
    api.buys.append(response)
    return v2.prepare_purchase(job, "a pack of Trident gum from Amazon", budget, ADDRESS)


def test_prepare_never_spends(api):
    out = prepare(api)
    assert out["status"] == "prepared" and not api.confirms()
    q = out["quote"]
    assert (q["currency"], q["subtotal_cents"], q["ceiling_cents"], q["budget_cents"]) == ("usd", 132, 1206, 1500)
    assert v2.inspect_purchase("job_1") == out


def test_confirm_needs_settled_order_not_just_an_order_id(api):
    quote_id = prepare(api)["quote"]["quote_id"]
    api.buys.append(PLACED)
    api.convs += [{"turn_in_progress": False, "orders": [{**SETTLED["orders"][0], "status": "confirming"}]}, SETTLED]
    out = v2.confirm_purchase("job_1", quote_id)
    assert (out["status"], out["reason"]) == ("pending", "charge_confirming")
    assert api.confirms() == [{"conversation_id": "conv_1", "confirm": "h1", "payment_source": "vault"}]
    out = v2.inspect_purchase("job_1")
    assert out["status"] == "confirmed" and out["order"]["order_id"] == "ord_1" and out["order"]["total_cents"] == 270
    assert v2.confirm_purchase("job_1", quote_id) == out and len(api.confirms()) == 1


def test_vault_approval_then_same_confirm(api):
    quote_id = prepare(api)["quote"]["quote_id"]
    api.buys += [(200, {"status": "declined", "decline_code": "vault_approval_required", "charge_status": "none",
                        "approval_url": "https://vault.agentcard.sh/authorize?id=cauth_1"}), PLACED]
    api.convs.append(SETTLED)
    out = v2.confirm_purchase("job_1", quote_id)
    assert out["status"] == "approval_required" and out["approval_url"].startswith("https://")
    assert v2.confirm_purchase("job_1", quote_id)["status"] == "confirmed" and len(api.confirms()) == 2


def test_approval_landing_is_found_by_inspect(api):
    quote_id = prepare(api)["quote"]["quote_id"]
    api.buys.append((200, {"status": "declined", "decline_code": "vault_approval_required", "charge_status": "none"}))
    api.convs.append(SETTLED)
    v2.confirm_purchase("job_1", quote_id)
    assert v2.inspect_purchase("job_1")["status"] == "confirmed" and len(api.confirms()) == 1


@pytest.mark.parametrize("cart,reason", [
    ({**CART, "approvedCeilingCents": 2348}, "over_budget"),
    ({**CART, "approvedCeilingCents": None}, "ambiguous_amount"),
    ({**CART, "totalCents": "132"}, "ambiguous_amount"),
    ({**CART, "merchant_currency": "cad"}, "unsupported_currency"),
])
def test_unconfirmable_carts_block_confirmation(api, cart, reason):
    out = prepare(api, response=(200, {"conversation_id": "conv_1", "cart": cart}))
    assert (out["status"], out["reason"]) == ("failed_no_purchase", reason)
    assert v2.confirm_purchase("job_1", out["quote"]["quote_id"]) == out and not api.confirms()


def test_changed_cart_needs_fresh_approval(api):
    old = prepare(api)["quote"]["quote_id"]
    api.buys.append((409, {"error": "price_changed", "cart": {**CART, "hash": "h2", "totalCents": 150,
                                                               "approvedCeilingCents": 1230}}))
    out = v2.confirm_purchase("job_1", old)
    assert (out["status"], out["reason"]) == ("approval_required", "cart_changed")
    new = out["quote"]["quote_id"]
    assert new != old and out["quote"]["subtotal_cents"] == 150
    assert v2.confirm_purchase("job_1", old)["reason"] == "quote_changed" and len(api.confirms()) == 1
    api.buys.append(PLACED)
    api.convs.append(SETTLED)
    assert v2.confirm_purchase("job_1", new)["status"] == "confirmed"
    assert [c["confirm"] for c in api.confirms()] == ["h1", "h2"]


def test_timeout_is_inspected_never_repeated(api):
    quote_id = prepare(api)["quote"]["quote_id"]
    api.buys.append(requests.Timeout("read timed out"))
    api.convs += [{"turn_in_progress": True}, SETTLED]
    assert v2.confirm_purchase("job_1", quote_id)["reason"] == "in_progress"
    assert v2.confirm_purchase("job_1", quote_id)["status"] == "confirmed"
    assert len(api.confirms()) == 1


def test_duplicate_requests(api):
    first = prepare(api)
    assert v2.prepare_purchase("job_1", "a pack of Trident gum from Amazon", 15, ADDRESS) == first
    assert len(api.calls) == 1
    with pytest.raises(ValueError):
        v2.prepare_purchase("job_1", "a pack of Trident gum from Amazon", 20, ADDRESS)
    with pytest.raises(ValueError):
        v2.inspect_purchase("job_unknown")


def test_restart_after_confirm_sent_inspects(api):
    quote_id = prepare(api)["quote"]["quote_id"]
    api.buys.append(SystemExit("process killed mid-confirm"))
    with pytest.raises(SystemExit):
        v2.confirm_purchase("job_1", quote_id)
    assert v2._load()["job_1"]["attempts"][0]["state"] == "sent"
    api.convs.append(SETTLED)
    assert v2.confirm_purchase("job_1", quote_id)["status"] == "confirmed" and len(api.confirms()) == 1


def test_restart_during_prepare_is_no_purchase(api):
    api.buys.append(SystemExit("process killed mid-prepare"))
    with pytest.raises(SystemExit):
        v2.prepare_purchase("job_1", "gum", 15, ADDRESS)
    assert v2.inspect_purchase("job_1")["reason"] == "prepare_interrupted"


def test_partial_order_is_not_success(api):
    quote_id = prepare(api)["quote"]["quote_id"]
    api.buys.append((200, {"status": "partially_placed", "order_id": None, "charge_status": "settled",
                           "placements": [{"merchant": "retail", "order_id": "ord_1"}]}))
    out = v2.confirm_purchase("job_1", quote_id)
    assert out["status"] == "partial" and v2.inspect_purchase("job_1") == out


def test_sandbox_decline_is_no_purchase(api):
    quote_id = prepare(api)["quote"]["quote_id"]
    api.buys.append((200, {"status": "declined", "decline_code": "sandbox_mode", "charge_status": "none"}))
    out = v2.confirm_purchase("job_1", quote_id)
    assert (out["status"], out["decline_code"]) == ("failed_no_purchase", "sandbox_mode")


def test_decline_without_charge_status_is_resolved_from_conversation(api):
    quote_id = prepare(api)["quote"]["quote_id"]
    api.buys.append((200, {"status": "declined", "decline_code": "sandbox_mode"}))
    api.convs.append({"turn_in_progress": False, "orders": [],
                      "last_checkout": {"status": "denied", "decline_code": "sandbox_mode", "charge_status": "none"}})
    assert v2.confirm_purchase("job_1", quote_id)["status"] == "failed_no_purchase"


def test_server_error_is_pending_not_refundable(api):
    quote_id = prepare(api)["quote"]["quote_id"]
    api.buys.append((502, {"error": "bad gateway"}))
    api.convs.append({"turn_in_progress": False, "orders": [], "last_checkout": None})
    assert v2.confirm_purchase("job_1", quote_id)["status"] == "pending"


@pytest.mark.parametrize("response,status", [
    ((200, {"conversation_id": "conv_1", "status": "needs_input", "reply": "Which flavor?"}), "needs_input"),
    ((200, {"conversation_id": "conv_1", "status": "done", "reply": "Nothing matched"}), "failed_no_purchase"),
    ((500, {"error": "boom"}), "failed_no_purchase"),
])
def test_prepare_without_cart(api, response, status):
    assert prepare(api, response=response)["status"] == status and not api.confirms()
