"""Offline tests for purchase.py (v1 contract): a possible charge must never come back as "failed"."""
import json
import sys
from pathlib import Path

import pytest
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import agentcard  # noqa: E402
import purchase as v1  # noqa: E402

ADDRESS = {"street": "1 Test St", "city": "San Francisco", "state": "CA", "zip": "94123", "phone": "+14155550100",
           "name": "Test Buyer"}
CART = {"hash": "h1", "merchant_name": "Amazon", "totalCents": 132, "approvedCeilingCents": 1206,
        "items": [{"name": "Trident gum", "qty": 1, "priceCents": 132}]}
QUOTED = (200, {"conversation_id": "conv_1", "status": "needs_input", "cart": CART})


class Fake:
    def __init__(self):
        self.buys, self.convs, self.calls = [], [], []

    def buy(self, body, timeout=150):
        self.calls.append(body)
        r = self.buys.pop(0)
        if isinstance(r, BaseException):
            raise r
        return r

    def conversation(self, cid):
        return self.convs.pop(0)


@pytest.fixture
def api(tmp_path, monkeypatch):
    fake = Fake()
    monkeypatch.setattr(v1, "LEDGER", tmp_path / "purchases.json")
    monkeypatch.setattr(v1, "buy", fake.buy)
    monkeypatch.setattr(v1, "conversation", fake.conversation)
    monkeypatch.setattr(v1.time, "sleep", lambda s: None)
    return fake


def run(api, confirm, convs=(), rid="job_1"):
    api.buys += [QUOTED, confirm]
    api.convs += list(convs)
    return v1.purchase("gum", 10, ADDRESS, request_id=rid)


def test_order_id_is_success(api):
    out = run(api, (200, {"status": "order_placed", "order_id": "ord_1"}))
    assert out["status"] == "success" and out["order_id"] == "ord_1"


def test_sandbox_decline_fails_without_conversation_read(api):
    out = run(api, (200, {"decline_code": "sandbox_mode", "charge_status": "none"}))
    assert out == {**out, "status": "failed", "reason": "sandbox_mode"}


def test_decline_without_charge_status_is_checked_not_refunded(api):
    out = run(api, (200, {"decline_code": "card_declined", "status": "needs_input"}),
              convs=[{"turn_in_progress": False, "last_checkout": {"status": "confirming"}}])
    assert out["status"] == "pending"


def test_explicit_no_charge_decline_fails(api):
    out = run(api, (200, {"decline_code": "card_declined", "charge_status": "none"}))
    assert out["status"] == "failed" and out["reason"] == "declined"


@pytest.mark.parametrize("last", [None, {"status": "confirming"}, {"status": "settled"}, {"status": "needs_approval"}])
def test_unclear_checkout_after_timeout_stays_pending(api, last):
    out = run(api, requests.Timeout(), convs=[{"turn_in_progress": False, "last_checkout": last}])
    assert out["status"] == "pending"


def test_order_in_conversation_after_5xx_is_success(api):
    out = run(api, (502, {}), convs=[{"turn_in_progress": False, "orders": [{"order_id": "ord_9", "status": "settled"}]}])
    assert out["status"] == "success" and out["order_id"] == "ord_9"


def test_denied_with_no_charge_in_conversation_fails(api):
    out = run(api, (502, {}), convs=[{"turn_in_progress": False,
                                      "last_checkout": {"status": "denied", "charge_status": "none"}}])
    assert out["status"] == "failed"


def test_partial_order_is_not_success(api):
    assert run(api, (200, {"status": "partially_placed", "order_id": "ord_1"}))["status"] == "pending"


def test_409_without_cart_is_reconciled(api):
    out = run(api, (409, {"error": "conflict"}), convs=[{"turn_in_progress": False, "orders": [{"order_id": "o"}]}])
    assert out["status"] == "success"


def test_order_placed_without_id_reads_conversation(api):
    out = run(api, (200, {"status": "order_placed"}), convs=[{"turn_in_progress": False, "orders": [{"order_id": "o2"}]}])
    assert out["order_id"] == "o2"


def test_missing_ledger_record_is_pending_not_refund(api):
    assert v1.inspect_purchase("never_seen")["status"] == "pending"


def test_corrupt_ledger_never_raises(api):
    v1.LEDGER.write_text("{truncated")
    assert v1.purchase("gum", 10, ADDRESS, request_id="job_1")["status"] == "pending"
    assert v1.inspect_purchase("job_1")["status"] == "pending"
    assert api.calls == []


def test_repeat_request_never_buys_twice(api):
    run(api, (200, {"order_id": "ord_1"}))
    assert v1.purchase("gum", 10, ADDRESS, request_id="job_1")["order_id"] == "ord_1"
    assert len(api.calls) == 2


def test_inspect_resolves_pending_later(api):
    assert run(api, requests.Timeout(), convs=[{"turn_in_progress": True}] * 20)["status"] == "pending"
    api.convs.append({"turn_in_progress": False, "orders": [{"order_id": "ord_1"}]})
    assert v1.inspect_purchase("job_1")["status"] == "success"
    assert json.loads(v1.LEDGER.read_text())["job_1"]["outcome"]["status"] == "success"


def test_token_refresh_is_atomic_and_locked(tmp_path, monkeypatch):
    tokens = tmp_path / "tokens.json"
    tokens.write_text(json.dumps({"user_id": "u", "access_token": "old", "refresh_token": "r1", "expires_at": 0}))
    monkeypatch.setattr(agentcard, "TOKENS", tokens)
    monkeypatch.setattr(agentcard, "LOCKFILE", tmp_path / "lock")
    monkeypatch.setattr(agentcard, "REFRESH_MARKER", tmp_path / "pending")
    sent = []
    monkeypatch.setattr(agentcard, "org", lambda m, p, json: sent.append(json) or
                        {"access_token": "new", "refresh_token": "r2", "expires_in": 3600})
    assert agentcard.user_token() == "new"
    assert agentcard.user_token() == "new" and len(sent) == 1
    saved = json.loads(tokens.read_text())
    assert saved["refresh_token"] == "r2" and (tokens.stat().st_mode & 0o777) == 0o600
    assert not (tmp_path / "pending").exists()


def test_unfinished_refresh_blocks_another(tmp_path, monkeypatch):
    tokens = tmp_path / "tokens.json"
    tokens.write_text(json.dumps({"user_id": "u", "access_token": "a", "refresh_token": "r1", "expires_at": 0}))
    monkeypatch.setattr(agentcard, "TOKENS", tokens)
    monkeypatch.setattr(agentcard, "LOCKFILE", tmp_path / "lock")
    monkeypatch.setattr(agentcard, "REFRESH_MARKER", tmp_path / "pending")
    (tmp_path / "pending").write_text("{}")
    monkeypatch.setattr(agentcard, "org", lambda *a, **k: pytest.fail("must not spend the refresh token"))
    with pytest.raises(RuntimeError):
        agentcard.user_token()
