"""Fixes from the 2026-10-07 live-demo gap review."""
import httpx
import pytest

from cardano_card.api import create_app
from cardano_card.engine import Engine
from cardano_card.hosted_escrow import HostedMasumiEscrow
from cardano_card.models import StartRequest
from cardano_card.ops import GuardedPurchaser
from cardano_card.providers import FakeEscrow, FakePurchaser
from cardano_card.store import Store
from test_lifecycle import PAYLOAD

TOKEN = "local-test-token-not-a-wallet-key"


def test_store_lists_jobs_in_creation_order(tmp_path):
    store = Store(str(tmp_path / "order.db"))
    try:
        for i in range(30):
            store.save_job({"id": f"{i:02d}-zzzz" if i % 2 else f"{i:02d}-aaaa", "phase": "x"}, "created")
        assert [j["id"][:2] for j in store.jobs()] == [f"{i:02d}" for i in range(30)]
    finally:
        store.close()


async def test_reconciling_is_throttled_and_escalates_at_the_result_deadline(tmp_path):
    store = Store(str(tmp_path / "rec.db"))
    now = [1_000_000.0]
    engine = Engine(store, FakeEscrow(store), FakePurchaser(store, "unknown"), clock=lambda: now[0])
    engine.INSPECT_SECONDS = 30
    calls = []
    original = engine.purchaser.inspect

    async def counted(request_id):
        calls.append(now[0])
        return await original(request_id)
    engine.purchaser.inspect = counted
    try:
        job = await engine.start(StartRequest.model_validate(PAYLOAD))
        await engine.tick()
        await engine.simulate(job["id"], "fund")
        await engine.tick()
        assert engine.get(job["id"])["phase"] == "reconciling"
        for _ in range(5):
            now[0] += 2
            await engine.tick()
        assert len(calls) == 1, "inspected on every tick"
        now[0] += 30
        await engine.tick()
        assert len(calls) == 2
        now[0] = engine.get(job["id"])["payment"]["submitResultTime"] - 30
        await engine.tick()
        job = engine.get(job["id"])
        assert job["phase"] == "manual_review" and "resolve from /ops" in store.events(job["id"])[-1]["message"]
    finally:
        store.close()


async def test_resolve_refuses_a_job_that_holds_an_order(tmp_path):
    store = Store(str(tmp_path / "res.db"))
    engine = Engine(store, FakeEscrow(store), GuardedPurchaser(FakePurchaser(store), store))
    try:
        app = create_app(engine, TOKEN, background=False)
        async with app.router.lifespan_context(app), httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            client.headers["Authorization"] = "Bearer " + TOKEN
            job = await engine.start(StartRequest.model_validate(PAYLOAD))
            await engine.tick()
            await engine.simulate(job["id"], "fund")
            await engine.tick()
            await engine.tick()
            job = engine.get(job["id"])
            assert job["outcome"]["status"] == "success" and job["result"]
            engine.change(job, "manual_review", "pretend the result deadline passed after the order")
            r = await client.post(f"/ops/jobs/{job['id']}/resolve", json={"note": "oops"})
            assert r.status_code == 409 and "confirmed order" in r.json()["detail"]
            assert engine.get(job["id"])["phase"] == "manual_review"
    finally:
        store.close()


async def test_hosted_non_json_reply_is_a_transport_error_not_an_invalid_payment(monkeypatch):
    escrow = HostedMasumiEscrow("https://app.masumi.network/pay/api/v1/", "k" * 32, "a" * 70, "b" * 56,
                                "addr_test1qpgq4gf9fmzg3gkdnp9jdtujtscr2qdms07tmtp8aqz5ykc7uvlelkeuqsgctx9n4en6xmkf4qhqkumzcc3qwt4845hqsxwadj",
                                "20000000", "0", lovelace_per_usd="fixed")

    class Reply:
        status_code = 200
        def raise_for_status(self): pass
        def json(self): raise ValueError("Expecting value")

    with pytest.raises(ConnectionError):
        escrow._body(Reply())

    class Odd(Reply):
        def json(self): return {"status": "error"}

    with pytest.raises(ConnectionError):
        escrow._body(Odd())
