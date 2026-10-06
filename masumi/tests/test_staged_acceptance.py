import pytest

from cardano_card.models import StartRequest
from cardano_card.providers import FakeEscrow
from cardano_card.staged_acceptance import StagedRunner
from cardano_card.staged_engine import StagedEngine
from cardano_card.staged_purchase import FakeStagedModule
from cardano_card.store import Store
from test_lifecycle import PAYLOAD


class FakeBuyer:
    def __init__(self, engine):
        self.engine, self.writes = engine, []

    async def fund(self, request, terms):
        if "fund" not in self.writes:
            self.writes.append("fund")
            await self.engine.simulate(terms["id"], "fund")

    async def refund(self, job_id, status):
        assert status["phase"] == "refund_due" and status["purchase"]["status"] == "failed"
        if "refund" not in self.writes:
            self.writes.append("refund")
            await self.engine.simulate(job_id, "request_refund")


@pytest.fixture
def runner(tmp_path):
    store = Store(str(tmp_path / "staged.db"))

    def make(scenario):
        engine = StagedEngine(store, FakeEscrow(store), FakeStagedModule(store, scenario), escrow_lovelace=10_000_000,
                              payout_address="SIM-payout")
        buyer = FakeBuyer(engine)
        observed = []

        async def observe(job):
            observed.append(job["phase"])
            return {"tx_hashes": ["ab" * 32]}
        return engine, buyer, StagedRunner(engine, buyer, observe, StartRequest.model_validate(PAYLOAD))
    yield make
    store.close()


async def drive(engine, runner, until, steps=12):
    job = await engine.start(StartRequest.model_validate(PAYLOAD))
    for _ in range(steps):
        job = await runner.step(job["id"])
        if job["phase"] == "result_submitted":
            await engine.simulate(job["id"], "withdraw")
        if job["phase"] in until:
            break
    return job


async def test_quote_approval_funding_and_payout(runner):
    engine, buyer, staged = runner("success")
    job = await drive(engine, staged, {"paid"})
    assert job["phase"] == "paid" and buyer.writes == ["fund"]
    assert engine.store.get("staged_fake", job["id"])["confirmations"] == 1
    assert job["chain_transactions"][0]["reported_by"] == "masumi-buyer-node"


async def test_definitive_decline_is_refunded_by_the_buyer(runner):
    engine, buyer, staged = runner("declined")
    job = await drive(engine, staged, {"refunded"})
    assert job["phase"] == "refunded" and buyer.writes == ["fund", "refund"] and job["result"] is None


async def test_over_budget_quote_never_funds(runner):
    engine, buyer, staged = runner("over_budget")
    job = await drive(engine, staged, {"quote_rejected"})
    assert job["phase"] == "quote_rejected" and job["payment"] is None and buyer.writes == []


@pytest.mark.parametrize("status", ["failed_no_purchase", "needs_input"])
async def test_no_usable_cart_ends_the_job_before_escrow(runner, status):
    engine, buyer, staged = runner("success")

    async def prepare(**kw):
        return {"status": status, "job_id": kw["job_id"], "reason": "no_cart"}
    engine.module.prepare_purchase = prepare
    job = await drive(engine, staged, {"quote_rejected"})
    assert job["phase"] == "quote_rejected" and job["payment"] is None and buyer.writes == []
