import asyncio
import copy

import pytest

from cardano_card.agentcard_bridge import AgentCardPurchaser, ReplayTransport, MasonClientTransport
from cardano_card.engine import Engine
from cardano_card.models import StartRequest, ProvideInput, parse_purchase_input
from cardano_card.providers import FakeEscrow
from cardano_card.store import Store
from test_lifecycle import PAYLOAD

INPUT = parse_purchase_input(PAYLOAD["input_data"]).model_dump(mode="json")


@pytest.fixture
def bridge(tmp_path):
    store = Store(str(tmp_path / "bridge.db"))
    transport = ReplayTransport(store)
    purchaser = AgentCardPurchaser(store, transport)
    yield purchaser
    store.close()


async def test_order_placement_waits_for_merchant_evidence(bridge):
    first = await bridge.purchase(INPUT, "job")
    assert first["status"] == "pending" and first["reason"] == "processing"
    result = await bridge.inspect("job")
    assert result["status"] == "success" and result["total_usd"] == "7.5"
    assert (await bridge.purchase(INPUT, "job")) == result
    assert bridge.store.get("agentcard_replay", "job")["confirms"] == 1


async def test_concurrent_calls_and_changed_payload(bridge):
    await asyncio.gather(*(bridge.purchase(INPUT, "job") for _ in range(8)))
    assert bridge.store.get("agentcard_replay", "job")["confirms"] == 1
    changed = {**INPUT, "ask": "Different item"}
    with pytest.raises(ValueError, match="different inputs"):
        await bridge.purchase(changed, "job")


async def test_lost_confirmation_recovers_after_database_reopen(tmp_path):
    path = str(tmp_path / "restart.db")
    store = Store(path)
    bridge = AgentCardPurchaser(store, ReplayTransport(store, "timeout_recovered"))
    assert (await bridge.purchase(INPUT, "job"))["reason"] == "unknown"
    store.close()
    store = Store(path)
    try:
        bridge = AgentCardPurchaser(store, ReplayTransport(store))
        assert (await bridge.purchase(INPUT, "job"))["reason"] == "processing"
        assert (await bridge.inspect("job"))["status"] == "success"
        assert store.get("agentcard_replay", "job")["confirms"] == 1
    finally:
        store.close()


@pytest.mark.parametrize("scenario", ["unknown", "partial"])
async def test_uncertain_order_never_becomes_refundable_failure(bridge, scenario):
    bridge.transport.scenario = scenario
    assert (await bridge.purchase(INPUT, "job"))["status"] == "pending"
    for _ in range(3):
        assert (await bridge.inspect("job"))["status"] == "pending"
    assert bridge.store.get("agentcard_replay", "job")["confirms"] == 1


async def test_approval_resumes_exact_cart_and_address(bridge):
    bridge.transport.scenario = "approval"
    calls = []
    original = bridge.transport.buy

    async def capture(body, request_id):
        calls.append(copy.deepcopy(body))
        return await original(body, request_id)

    bridge.transport.buy = capture
    assert (await bridge.purchase(INPUT, "job"))["reason"] == "approval_required"
    await bridge.purchase(INPUT, "job", {"approved": True})
    assert calls[1] == calls[2]
    assert all(c["delivery_address"] == INPUT["address"] for c in calls)
    assert (await bridge.inspect("job"))["status"] == "success"


async def test_rejected_approval_never_reconfirms(bridge):
    bridge.transport.scenario = "approval"
    await bridge.purchase(INPUT, "job")
    assert (await bridge.purchase(INPUT, "job", {"approved": False}))["reason"] == "cancelled"
    assert bridge.store.get("agentcard_replay", "job")["confirms"] == 1


async def test_clarification_uses_same_conversation(bridge):
    bridge.transport.scenario = "needs_input"
    assert (await bridge.purchase(INPUT, "job"))["reason"] == "needs_input"
    assert (await bridge.purchase(INPUT, "job", {"answer": "Choose coffee"}))["reason"] == "processing"
    assert (await bridge.inspect("job"))["status"] == "success"


@pytest.mark.parametrize("total,budget", [(5010, "100.00"), (1001, "10.00")])
async def test_budget_and_hard_card_cap_block_confirm(bridge, total, budget):
    original = bridge.transport.buy
    calls = []

    async def inflated(body, request_id):
        calls.append(body)
        code, data = await original(body, request_id)
        data["cart"]["totalCents"] = total
        return code, data

    bridge.transport.buy = inflated
    assert (await bridge.purchase({**INPUT, "max_total_usd": budget}, "job"))["reason"] == "over_budget"
    assert len(calls) == 1 and "confirm" not in calls[0]


@pytest.mark.parametrize("currency", [None, "EUR"])
async def test_missing_or_wrong_currency_never_confirms(bridge, currency):
    original = bridge.transport.buy

    async def wrong_currency(body, request_id):
        code, data = await original(body, request_id)
        data["cart"]["currency"] = currency
        return code, data

    bridge.transport.buy = wrong_currency
    assert (await bridge.purchase(INPUT, "job"))["status"] == "pending"
    assert bridge.store.get("agentcard_replay", "job")["confirms"] == 0


@pytest.mark.parametrize("ceiling", [2348, 5100])
async def test_estimated_total_cannot_hide_higher_approved_ceiling(bridge, ceiling):
    original = bridge.transport.buy
    async def estimated(body, request_id):
        code, data = await original(body, request_id)
        data["cart"].update(totalCents=1241, estimatedTotalCents=1241,
                            totalIsEstimate=True, approvedCeilingCents=ceiling)
        data["cart"].pop("currency")
        return code, data
    bridge.transport.buy = estimated
    result = await bridge.purchase({**INPUT, "max_total_usd": "20.00"}, "job")
    assert result == {"status": "failed", "reason": "over_budget"}
    assert bridge.store.get("agentcard_replay", "job")["confirms"] == 0
    record = bridge.store.get("agentcard_purchases", "job")
    assert record["cart"]["approvedCeilingCents"] == ceiling
    assert record["budget_evidence"]["currency_verified"] is False


@pytest.mark.parametrize("ceiling", [None, True, "900"])
async def test_estimated_total_needs_valid_approval_ceiling(bridge, ceiling):
    original = bridge.transport.buy
    async def estimated(body, request_id):
        code, data = await original(body, request_id)
        data["cart"].update(totalIsEstimate=True, approvedCeilingCents=ceiling)
        return code, data
    bridge.transport.buy = estimated
    assert (await bridge.purchase(INPUT, "job"))["status"] == "pending"
    assert bridge.store.get("agentcard_replay", "job")["confirms"] == 0


async def test_saved_validation_pause_can_reject_recovered_cart_without_buy(bridge):
    original = bridge.transport.buy
    async def no_currency(body, request_id):
        code, data = await original(body, request_id)
        data["cart"].pop("currency")
        return code, data
    bridge.transport.buy = no_currency
    assert (await bridge.purchase(INPUT, "job"))["status"] == "pending"
    record = bridge.store.get("agentcard_purchases", "job")
    # Legacy checkpoint from before candidate carts were saved.
    record.update(cart=None, stage="cart_requested")
    bridge.save(record)
    async def conversation(cid, request_id):
        return {"conversation_id": cid, "turn_in_progress": False, "orders": [], "last_checkout": None,
                "carts": [{"hash": "same-conversation-cart", "totalCents": 750,
                           "totalIsEstimate": True, "approvedCeilingCents": 1500}]}
    bridge.transport.conversation = conversation
    assert (await bridge.inspect("job"))["reason"] == "over_budget"
    assert bridge.store.get("agentcard_replay", "job")["writes"] == 1
    assert bridge.store.get("agentcard_replay", "job")["confirms"] == 0


async def test_lost_initial_response_does_not_create_second_cart(bridge):
    calls = 0

    async def timeout(*args):
        nonlocal calls
        calls += 1
        raise TimeoutError()

    bridge.transport.buy = timeout
    await bridge.purchase(INPUT, "job")
    assert (await bridge.purchase(INPUT, "job"))["status"] == "pending"
    assert calls == 1


async def test_busy_409_is_not_a_refundable_failure(bridge):
    original = bridge.transport.buy

    async def busy(body, request_id):
        return (409, {"code": "turn_in_progress"}) if "confirm" in body else await original(body, request_id)

    bridge.transport.buy = busy
    assert (await bridge.purchase(INPUT, "job"))["reason"] == "unknown"


@pytest.mark.parametrize("evidence", [
    {"merchant_confirmed": False}, {"order_id": "wrong"}, {"total_cents": 1001}, {"currency": "EUR"},
])
async def test_unverified_or_over_budget_final_order_cannot_settle(bridge, evidence):
    original = bridge.transport.track

    async def invalid(order_id, request_id):
        return {**await original(order_id, request_id), **evidence}

    bridge.transport.track = invalid
    await bridge.purchase(INPUT, "job")
    assert (await bridge.inspect("job"))["status"] == "pending"


@pytest.mark.parametrize("scenario,expected", [("success", "paid"), ("declined", "refunded"), ("timeout_recovered", "paid")])
async def test_full_engine_bridge_lifecycle(bridge, scenario, expected):
    bridge.transport.scenario = scenario
    engine = Engine(bridge.store, FakeEscrow(bridge.store), bridge)
    job = await engine.start(StartRequest.model_validate(PAYLOAD))
    await engine.tick()
    assert bridge.store.get("agentcard_purchases", job["id"]) is None
    await engine.simulate(job["id"], "fund")
    for _ in range(8):
        await engine.tick()
        phase = engine.get(job["id"])["phase"]
        if phase == "refund_due":
            await engine.simulate(job["id"], "request_refund")
        if phase == "result_submitted":
            await engine.simulate(job["id"], "withdraw")
    assert engine.get(job["id"])["phase"] == expected
    assert bridge.store.get("agentcard_replay", job["id"])["confirms"] == 1


async def test_network_transport_requires_positive_sandbox_verification():
    from unittest.mock import Mock, AsyncMock
    client = Mock()
    transport = MasonClientTransport(client, AsyncMock(return_value=False), AsyncMock(), exclusive_access=lambda: True)
    with pytest.raises(ValueError, match="Sandbox"):
        await transport.buy({}, "job")
    client.buy.assert_not_called()


@pytest.mark.parametrize("operation,args", [("buy", ({}, "job")), ("conversation", ("conv", "job")), ("track", ("order", "job"))])
async def test_shared_tokens_require_handoff_even_for_reads(operation, args):
    from unittest.mock import Mock, AsyncMock
    client, verify, track = Mock(), AsyncMock(), AsyncMock()
    transport = MasonClientTransport(client, verify, track)
    with pytest.raises(ValueError, match="handoff"):
        await getattr(transport, operation)(*args)
    assert client.mock_calls == []
    verify.assert_not_called()
    track.assert_not_called()


async def test_unexpected_order_during_cart_request_never_refunds(bridge):
    async def unexpected(*args):
        return 200, {"conversation_id": "surprise", "status": "order_placed", "order_id": "already-placed"}
    bridge.transport.buy = unexpected
    assert (await bridge.purchase(INPUT, "job"))["status"] == "pending"
    assert (await bridge.inspect("job"))["status"] == "pending"
    assert bridge.store.get("agentcard_purchases", "job")["stage"] == "review"


async def test_partial_order_does_not_become_success_from_single_order_reference(bridge):
    bridge.transport.scenario = "partial"
    await bridge.purchase(INPUT, "job")
    async def one_order(*args):
        return {"id": "SIM-CONV-job", "turn_in_progress": False, "orders": [{"id": "SIM-ORDER-job"}]}
    bridge.transport.conversation = one_order
    for _ in range(3):
        assert (await bridge.inspect("job"))["status"] == "pending"
