import copy
from decimal import Decimal

import pytest
from pydantic import ValidationError

from cardano_card.engine import Conflict, Engine
from cardano_card.models import ProvideInput, StartRequest
from cardano_card.providers import FakeEscrow, FakePurchaser
from cardano_card.store import Store


PAYLOAD = {"identifier_from_purchaser": "a" * 26, "input_data": {
    "ask": "Buy a sample", "max_total_usd": "10.00", "street": "123 Example St",
    "city": "Example", "state": "CA", "zip": "00000", "phone": "+12025550123", "name": "Demo"}}


@pytest.fixture
def engine(tmp_path):
    store = Store(str(tmp_path / "jobs.db"))
    result = Engine(store, FakeEscrow(store), FakePurchaser(store))
    yield result
    store.close()


async def start(engine):
    return await engine.start(StartRequest.model_validate(copy.deepcopy(PAYLOAD)))


async def fund(engine, job):
    await engine.simulate(job["id"], "fund")
    await engine.tick()
    return engine.get(job["id"])


async def test_unfunded_never_purchases(engine):
    job = await start(engine)
    await engine.tick()
    assert engine.get(job["id"])["phase"] == "awaiting_payment"
    assert engine.store.get("purchases", job["id"]) is None


async def test_success_separates_order_submission_and_payout(engine):
    job = await fund(engine, await start(engine))
    assert job["phase"] == "submitting_result"
    assert job["outcome"]["status"] == "success"
    await engine.tick()
    job = engine.get(job["id"])
    assert job["phase"] == "result_submitted"
    assert job["escrow_state"] == "ResultSubmitted"
    await engine.simulate(job["id"], "withdraw")
    await engine.tick()
    assert engine.get(job["id"])["phase"] == "paid"


async def test_duplicate_start_returns_same_job_and_rejects_changed_input(engine):
    first = await start(engine)
    assert (await start(engine))["id"] == first["id"]
    changed = copy.deepcopy(PAYLOAD)
    changed["input_data"]["max_total_usd"] = "11.00"
    with pytest.raises(Conflict):
        await engine.start(StartRequest.model_validate(changed))
    assert len(engine.store.jobs()) == 1


async def test_concurrent_start_deduplicates(engine):
    import asyncio
    jobs = await asyncio.gather(*(start(engine) for _ in range(10)))
    assert len({j["id"] for j in jobs}) == 1


@pytest.mark.parametrize("scenario", ["declined", "over_budget"])
async def test_failure_never_submits_success_and_needs_buyer_refund(engine, scenario):
    engine.purchaser.scenario = scenario
    job = await fund(engine, await start(engine))
    assert job["phase"] == "refund_due"
    assert job["result"] is None
    assert await engine.escrow.observe(job) == "FundsLocked"
    await engine.simulate(job["id"], "request_refund")
    await engine.tick()
    await engine.tick()
    assert engine.get(job["id"])["phase"] == "refunded"


@pytest.mark.parametrize("scenario,response", [("approval", {"approved": True}), ("needs_input", {"answer": "Blue"})])
async def test_input_resumes_original_purchase(engine, scenario, response):
    engine.purchaser.scenario = scenario
    job = await fund(engine, await start(engine))
    original = copy.deepcopy(job["input"])
    assert job["phase"] == "awaiting_input"
    await engine.provide(ProvideInput(job_id=job["id"], input_schema_hash=job["input_schema_hash"], input_data=response))
    await engine.tick()
    assert engine.get(job["id"])["outcome"]["status"] == "success"
    assert engine.get(job["id"])["input"] == original


async def test_stale_or_wrong_input_cannot_resume(engine):
    engine.purchaser.scenario = "approval"
    job = await fund(engine, await start(engine))
    with pytest.raises(Conflict):
        await engine.provide(ProvideInput(job_id=job["id"], input_schema_hash="0" * 64, input_data={"approved": True}))
    with pytest.raises(Conflict):
        await engine.provide(ProvideInput(job_id=job["id"], input_schema_hash=job["input_schema_hash"], input_data={"answer": "yes"}))


async def test_rejected_approval_becomes_definitive_failure(engine):
    engine.purchaser.scenario = "approval"
    job = await fund(engine, await start(engine))
    await engine.provide(ProvideInput(job_id=job["id"], input_schema_hash=job["input_schema_hash"], input_data={"approved": False}))
    await engine.tick()
    assert engine.get(job["id"])["phase"] == "refund_due"


async def test_ambiguous_charge_only_inspects_never_retries(engine):
    real = engine.purchaser

    class TimeoutAfterCharge:
        simulated = True
        calls = 0

        async def purchase(self, inputs, request_id, response=None):
            self.calls += 1
            await real.purchase(inputs, request_id, response)
            raise TimeoutError()

        async def inspect(self, request_id):
            return await real.inspect(request_id)

    wrapped = TimeoutAfterCharge()
    engine.purchaser = wrapped
    job = await fund(engine, await start(engine))
    assert job["phase"] == "reconciling"
    await engine.tick()
    await engine.tick()
    assert wrapped.calls == 1
    assert engine.get(job["id"])["phase"] == "result_submitted"


async def test_unknown_then_refund_requires_review(engine):
    engine.purchaser.scenario = "unknown"
    job = await fund(engine, await start(engine))
    await engine.simulate(job["id"], "request_refund")
    await engine.tick()
    assert engine.get(job["id"])["phase"] == "manual_review"
    assert await engine.escrow.observe(job) == "RefundRequested"


async def test_external_refund_does_not_hide_uncertain_order(engine):
    engine.purchaser.scenario = "unknown"
    job = await fund(engine, await start(engine))
    engine.store.put("escrow", job["id"], {"state": "RefundWithdrawn"})
    await engine.tick()
    assert engine.get(job["id"])["phase"] == "manual_review"


async def test_late_payment_after_expiry_requires_refund(engine):
    job = await start(engine)
    engine.clock = lambda: job["payment"]["payByTime"] + 1
    await engine.tick()
    assert engine.get(job["id"])["phase"] == "expired"
    engine.store.put("escrow", job["id"], {"state": "FundsLocked"})
    await engine.tick()
    assert engine.get(job["id"])["phase"] == "refund_due"
    assert engine.store.get("purchases", job["id"]) is None


async def test_deadline_prevents_new_purchase(engine):
    job = await start(engine)
    engine.clock = lambda: job["payment"]["submitResultTime"] - 30
    job = await fund(engine, job)
    assert job["phase"] == "refund_due"
    assert engine.store.get("purchases", job["id"]) is None


async def test_overbudget_success_is_not_misrepresented_as_no_purchase(engine):
    original = engine.purchaser.purchase

    async def bad_provider(*args):
        result = await original(*args)
        result["total_usd"] = "100.00"
        return result

    engine.purchaser.purchase = bad_provider
    job = await fund(engine, await start(engine))
    assert job["phase"] == "manual_review"
    assert job["outcome"]["status"] == "success"
    assert await engine.escrow.observe(job) == "FundsLocked"


async def test_submit_timeout_does_not_repurchase_or_claim_payout(engine):
    async def uncertain(*args):
        raise TimeoutError()

    engine.escrow.submit = uncertain
    job = await fund(engine, await start(engine))
    # An escrow timeout must not reclassify a confirmed order as an uncertain checkout.
    assert job["phase"] == "submitting_result"
    assert job["result"] is not None
    await engine.tick()
    assert engine.get(job["id"])["phase"] == "submitting_result"


async def test_crash_recovery_uses_saved_order_and_same_id(tmp_path):
    path = str(tmp_path / "recover.db")
    store = Store(path)
    engine = Engine(store, FakeEscrow(store), FakePurchaser(store))
    job = await start(engine)
    await engine.simulate(job["id"], "fund")
    job["purchase_started"] = True
    engine.change(job, "purchasing", "Simulated crash after charge, before saving result")
    await engine.purchaser.purchase(job["input"], job["id"])
    store.close()
    store = Store(path)
    try:
        recovered = Engine(store, FakeEscrow(store), FakePurchaser(store))
        await recovered.tick()
        await recovered.tick()
        assert recovered.get(job["id"])["phase"] == "result_submitted"
        assert (await start(recovered))["id"] == job["id"]
    finally:
        store.close()


async def test_approval_survives_restart(tmp_path):
    path = str(tmp_path / "approval.db")
    store = Store(path)
    engine = Engine(store, FakeEscrow(store), FakePurchaser(store, "approval"))
    job = await fund(engine, await start(engine))
    store.close()
    store = Store(path)
    try:
        recovered = Engine(store, FakeEscrow(store), FakePurchaser(store))
        await recovered.provide(ProvideInput(job_id=job["id"], input_schema_hash=job["input_schema_hash"], input_data={"approved": True}))
        await recovered.tick()
        await recovered.tick()
        assert recovered.get(job["id"])["phase"] == "result_submitted"
    finally:
        store.close()


async def test_payment_creation_timeout_reserves_id_and_never_recreates(engine):
    calls = []

    async def uncertain(job):
        calls.append(job["id"])
        raise TimeoutError()

    engine.escrow.create = uncertain
    first = await start(engine)
    assert first["phase"] == "payment_creation_unknown"
    assert (await start(engine))["id"] == first["id"]
    assert len(calls) == 1


@pytest.mark.parametrize("budget", [True, "NaN", "Infinity", "-1", "0", "1.001"])
def test_invalid_budget_rejected_before_payment(budget):
    payload = copy.deepcopy(PAYLOAD)
    payload["input_data"]["max_total_usd"] = budget
    with pytest.raises(ValidationError):
        StartRequest.model_validate(payload)


def test_database_rejects_second_worker(tmp_path):
    path = str(tmp_path / "jobs.db")
    store = Store(path)
    try:
        with pytest.raises(RuntimeError):
            Store(path)
    finally:
        store.close()
