"""Ezra's real StagedEngine + StagedModule driving purchase_v2 (only the Agentcard API is faked).

Needs masumi's dependencies; skipped in the plain agentcard venv. Run with masumi's venv:
  masumi/.venv/bin/python -m pytest agentcard/tests
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "agentcard"), str(ROOT / "masumi" / "src")]
pytest.importorskip("pytest_asyncio")
pytest.importorskip("cardano_card.staged_engine")

import purchase_v2 as v2  # noqa: E402
from cardano_card.models import ProvideInput, StartRequest  # noqa: E402
from cardano_card.providers import FakeEscrow  # noqa: E402
from cardano_card.staged_engine import StagedEngine  # noqa: E402
from cardano_card.staged_purchase import CheckoutOutcome, PreparedQuote, StagedModule  # noqa: E402
from cardano_card.store import Store  # noqa: E402
from test_purchase_v2 import CART, PLACED, QUOTED, SETTLED, FakeAgentcard  # noqa: E402

pytestmark = pytest.mark.asyncio
REQUEST = {"identifier_from_purchaser": "b" * 26, "input_data": {
    "ask": "a pack of Trident gum from Amazon", "max_total_usd": "15.00", "street": "1 Test St",
    "city": "San Francisco", "state": "CA", "zip": "94123", "phone": "+14155550100", "name": "Test Buyer"}}
DECLINED = (200, {"status": "needs_input", "decline_code": "sandbox_mode", "charge_status": "none"})


@pytest.fixture
def staged(tmp_path, monkeypatch):
    fake = FakeAgentcard()
    monkeypatch.setattr(v2, "LEDGER", tmp_path / "jobs.json")
    monkeypatch.setattr(v2, "buy", fake.buy)
    monkeypatch.setattr(v2, "conversation", fake.conversation)
    store = Store(str(tmp_path / "staged.db"))
    engine = StagedEngine(store, FakeEscrow(store), StagedModule("purchase_v2"), escrow_lovelace=10_000_000,
                          payout_address="SIM-payout")
    yield fake, engine
    store.close()


async def quoted(fake, engine, response=QUOTED):
    fake.buys.append(response)
    job = await engine.start(StartRequest.model_validate(REQUEST))
    await engine.tick()
    return engine.get(job["id"])


async def funded(fake, engine, *confirm_responses):
    job = await quoted(fake, engine)
    assert job["phase"] == "awaiting_quote_approval"
    PreparedQuote.model_validate(v2.inspect_purchase(job_id=job["id"]))
    await engine.provide(ProvideInput(job_id=job["id"], input_schema_hash=job["input_schema_hash"],
                                      input_data={"approved": True}))
    await engine.tick()
    assert engine.get(job["id"])["phase"] == "awaiting_payment" and not fake.confirms()
    fake.buys += confirm_responses
    await engine.simulate(job["id"], "fund")
    await engine.tick()
    return engine.get(job["id"])


async def test_success_reaches_payout(staged):
    fake, engine = staged
    fake.convs.append(SETTLED)
    job = await funded(fake, engine, PLACED)
    CheckoutOutcome.model_validate(v2.inspect_purchase(job_id=job["id"]))
    assert job["phase"] == "submitting_result" and job["outcome"]["order_id"] == "ord_1"
    await engine.tick()
    await engine.simulate(job["id"], "withdraw")
    await engine.tick()
    assert engine.get(job["id"])["phase"] == "paid" and len(fake.confirms()) == 1


async def test_sandbox_decline_reaches_refund(staged):
    fake, engine = staged
    job = await funded(fake, engine, DECLINED)
    assert job["phase"] == "refund_due" and job["result"] is None
    await engine.simulate(job["id"], "request_refund")
    await engine.tick()
    await engine.tick()
    assert engine.get(job["id"])["phase"] == "refunded"


async def test_charge_confirming_waits_then_pays(staged):
    fake, engine = staged
    fake.convs += [{"turn_in_progress": False, "orders": [{**SETTLED["orders"][0], "status": "confirming"}]}, SETTLED]
    job = await funded(fake, engine, PLACED)
    assert job["phase"] == "processing" and job["result"] is None
    await engine.tick()
    assert engine.get(job["id"])["phase"] == "submitting_result" and len(fake.confirms()) == 1


async def test_partial_is_never_refunded(staged):
    fake, engine = staged
    job = await funded(fake, engine, (200, {"status": "partially_placed", "charge_status": "settled"}))
    assert job["phase"] == "reconciling" and job["result"] is None
    await engine.tick()
    assert engine.get(job["id"])["phase"] == "reconciling" and len(fake.confirms()) == 1


async def test_over_budget_quote_is_rejected_before_escrow(staged):
    fake, engine = staged
    job = await quoted(fake, engine, (200, {**QUOTED[1], "cart": {**CART, "approvedCeilingCents": 2348}}))
    assert job["phase"] == "quote_rejected" and job["payment"] is None and not fake.confirms()
