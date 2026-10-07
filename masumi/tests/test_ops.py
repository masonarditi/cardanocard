import httpx
import pytest

from cardano_card.api import create_app
from cardano_card.engine import Engine
from cardano_card.models import StartRequest
from cardano_card.ops import GuardedPurchaser, card_enabled, set_card
from cardano_card.providers import FakeEscrow, FakePurchaser
from cardano_card.store import Store
from test_lifecycle import PAYLOAD

TOKEN = "local-test-token-not-a-wallet-key"


@pytest.fixture
def guarded(tmp_path):
    store = Store(str(tmp_path / "jobs.db"))
    engine = Engine(store, FakeEscrow(store), GuardedPurchaser(FakePurchaser(store), store))
    yield engine
    store.close()


async def funded(engine):
    job = await engine.start(StartRequest.model_validate(PAYLOAD))
    await engine.tick()
    await engine.simulate(job["id"], "fund")
    await engine.tick()
    return job


async def test_card_off_fails_without_calling_the_purchaser_and_refunds(guarded):
    calls = []
    inner = guarded.purchaser.inner
    original = inner.purchase

    async def spy(*args, **kwargs):
        calls.append(args)
        return await original(*args, **kwargs)
    inner.purchase = spy
    assert card_enabled(guarded.store)
    set_card(guarded.store, False, "demo rehearsal")
    job = await funded(guarded)
    await guarded.tick()
    job = guarded.get(job["id"])
    assert calls == []
    assert job["phase"] == "refund_due"
    assert job["outcome"] == {"status": "failed", "reason": "card_disabled"}
    assert guarded.store.get("ops", "log")[-1]["message"].endswith("OFF — demo rehearsal")


async def test_card_on_passes_through_and_inspect_is_never_blocked(guarded):
    set_card(guarded.store, False)
    set_card(guarded.store, True)
    job = await funded(guarded)
    await guarded.tick()
    assert guarded.get(job["id"])["outcome"]["status"] == "success"
    set_card(guarded.store, False)
    assert (await guarded.purchaser.inspect(job["id"]))["status"] == "success"
    assert guarded.purchaser.simulated is True and guarded.purchaser.module is None


async def test_ops_routes_are_token_gated_and_toggle_the_switch(tmp_path):
    store = Store(str(tmp_path / "ops.db"))
    engine = Engine(store, FakeEscrow(store), GuardedPurchaser(FakePurchaser(store), store))
    try:
        app = create_app(engine, TOKEN, background=False)
        async with app.router.lifespan_context(app), httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            page = await client.get("/ops")
            assert page.status_code == 200 and "Operator token" in page.text and TOKEN not in page.text
            assert page.headers["Content-Security-Policy"].startswith("default-src 'self'")
            assert (await client.get("/ops.js")).status_code == 200 and (await client.get("/ops.css")).status_code == 200
            assert (await client.get("/ops/status")).status_code == 401
            assert (await client.post("/ops/card", json={"enabled": False})).status_code == 401
            client.headers["Authorization"] = "Bearer " + TOKEN
            status = (await client.get("/ops/status", params={"probe": "false"})).json()
            assert status["card"]["enabled"] is True and status["probes"] == {} and status["jobs"] == [] and status["alerts"] == []
            assert status["config"]["escrow_rail"] == "simulated"
            flipped = (await client.post("/ops/card", json={"enabled": False, "note": "judges in the room"})).json()
            assert flipped["enabled"] is False
            job = await engine.start(StartRequest.model_validate(PAYLOAD))
            status = (await client.get("/ops/status", params={"probe": "false"})).json()
            assert status["card"]["enabled"] is False and status["card"]["note"] == "judges in the room"
            assert status["alerts"][0]["level"] == "warn" and "card_disabled" in status["alerts"][0]["text"]
            assert status["jobs"][0]["id"] == job["id"] and status["jobs"][0]["ask"] == PAYLOAD["input_data"]["ask"]
            assert "123 Example St" not in str(status) and status["events"][0]["job_id"] == job["id"]
            assert status["ops_log"][0]["message"] == "Card switched OFF — judges in the room"
    finally:
        store.close()


async def test_operator_can_resolve_a_pending_purchase_as_no_charge(tmp_path):
    store = Store(str(tmp_path / "resolve.db"))
    engine = Engine(store, FakeEscrow(store), GuardedPurchaser(FakePurchaser(store, "unknown"), store))
    try:
        app = create_app(engine, TOKEN, background=False)
        async with app.router.lifespan_context(app), httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            client.headers["Authorization"] = "Bearer " + TOKEN
            job = await funded(engine)
            await engine.tick()
            assert engine.get(job["id"])["phase"] == "reconciling"
            assert (await client.post(f"/ops/jobs/{job['id']}/resolve", json={"note": "x"})).status_code == 422
            resolved = (await client.post(f"/ops/jobs/{job['id']}/resolve", json={"note": "card shows no charge"})).json()
            assert resolved["phase"] == "refund_due" and resolved["purchase"] == {"status": "failed", "reason": "cancelled"}
            assert (await client.post(f"/ops/jobs/{job['id']}/resolve", json={"note": "again"})).status_code == 409
            assert (await client.post("/ops/jobs/missing/resolve", json={"note": "nope"})).status_code == 404
            await engine.tick()
            assert engine.get(job["id"])["phase"] == "refund_due"
            status = (await client.get("/ops/status", params={"probe": "false"})).json()
            assert status["ops_log"][0]["message"].startswith(f"Job {job['id'][:8]} resolved as no charge")
            await engine.simulate(job["id"], "request_refund")
            await engine.tick()
            await engine.tick()
            assert engine.get(job["id"])["phase"] == "refunded"
    finally:
        store.close()
