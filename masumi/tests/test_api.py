import httpx

from cardano_card.api import create_app
from cardano_card.engine import Engine
from cardano_card.providers import FakeEscrow, FakePurchaser
from cardano_card.store import Store
from test_lifecycle import PAYLOAD

TOKEN = "local-test-token-not-a-wallet-key"


async def test_event_feed_is_private_paginated_durable_and_logs_after_commit(tmp_path, capsys):
    from cardano_card.models import StartRequest
    path = str(tmp_path / "feed.db")
    store = Store(path)
    engine = Engine(store, FakeEscrow(store), FakePurchaser(store))
    try:
        app = create_app(engine, TOKEN, background=False, live_output=True)
        async with app.router.lifespan_context(app), httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            assert (await client.get("/events")).status_code == 401
            client.headers["Authorization"] = "Bearer " + TOKEN
            assert (await client.get("/events")).json() == {"events": [], "cursor": 0, "latest": 0}
            job = await engine.start(StartRequest.model_validate(PAYLOAD))
            page = (await client.get("/events", params={"after": 0, "limit": 1})).json()
            assert page["cursor"] == 1 and page["latest"] == 2
            assert page["events"][0]["phase"] == "creating_payment"
            second = (await client.get("/events", params={"after": page["cursor"]})).json()
            assert second["cursor"] == 2 and second["events"][0]["phase"] == "awaiting_payment"
            assert second["events"][0]["job_id"] == job["id"]
            assert "input" not in str(page) and "123 Example St" not in str(page)
            assert (await client.get("/events", params={"after": 999})).status_code == 409
            assert (await client.get("/events", params={"limit": 0})).status_code == 422
            output = capsys.readouterr().out
            assert "creating_payment" in output and "awaiting_payment" in output
            assert "SIMULATED" in output and TOKEN not in output
            seen = []
            def broken_sink(event):
                seen.append(store.event_feed(after=2)["events"][0])
                raise BrokenPipeError()
            store.event_sink = broken_sink
            store.save_job(job, "Visible after commit")
            assert seen[0]["sequence"] == 3
    finally:
        store.close()
    reopened = Store(path)
    try:
        assert reopened.event_feed(after=2)["events"][0]["message"] == "Visible after commit"
        assert reopened.event_feed(limit=1)["cursor"] == 3
    finally:
        reopened.close()


async def test_http_auth_schema_dedup_and_status(tmp_path):
    store = Store(str(tmp_path / "http.db"))
    engine = Engine(store, FakeEscrow(store), FakePurchaser(store))
    try:
        app = create_app(engine, TOKEN, background=False)
        async with app.router.lifespan_context(app), httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            assert (await client.get("/availability")).json()["simulated_escrow"] is True
            fields = (await client.get("/input_schema")).json()["input_data"]
            assert {f["id"] for f in fields} == set(PAYLOAD["input_data"])
            assert (await client.post("/start_job", json=PAYLOAD)).status_code == 401
            client.headers["Authorization"] = "Bearer " + TOKEN
            first = await client.post("/start_job", json=PAYLOAD)
            assert first.status_code == 200
            assert (await client.post("/start_job", json=PAYLOAD)).json()["id"] == first.json()["id"]
            job_id = first.json()["id"]
            assert (await client.get("/status", params={"job_id": job_id})).json()["status"] == "awaiting_payment"
            assert (await client.post(f"/local/jobs/{job_id}/fund")).status_code == 200
            assert (await client.get("/status", params={"job_id": "missing"})).status_code == 404
    finally:
        store.close()


def test_preprod_rejects_unverified_configuration(monkeypatch):
    import pytest
    from cardano_card.api import configured_engine
    monkeypatch.setenv("CARDANO_CARD_MODE", "Mainnet")
    with pytest.raises(ValueError, match="Only local and Preprod"):
        configured_engine()


async def test_replay_demo_and_private_evidence(tmp_path):
    from cardano_card.agentcard_bridge import AgentCardPurchaser, ReplayTransport
    store = Store(str(tmp_path / "replay.db"))
    engine = Engine(store, FakeEscrow(store), AgentCardPurchaser(store, ReplayTransport(store)))
    try:
        app = create_app(engine, TOKEN, background=False)
        async with app.router.lifespan_context(app), httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            page = await client.get("/")
            assert page.status_code == 200 and "Shopping request" in page.text
            assert TOKEN not in page.text
            assert "frame-ancestors 'none'" in page.headers["Content-Security-Policy"]
            assert (await client.get("/assets/app.js")).status_code == 200
            assert (await client.get("/session")).status_code == 401
            assert (await client.get("/jobs")).status_code == 401
            assert (await client.get("/evidence", params={"job_id": "missing"})).status_code == 401
            client.headers["Authorization"] = "Bearer " + TOKEN
            started = await client.post("/local/start_job", json={"request": PAYLOAD, "scenario": "timeout_recovered"})
            job_id = started.json()["id"]
            listing = (await client.get("/jobs")).json()["jobs"]
            assert listing[0]["id"] == job_id
            assert "input" not in listing[0] and "payment" not in listing[0]
            assert (await client.post("/local/start_job", json={"request": PAYLOAD, "scenario": "declined"})).status_code == 409
            await client.post(f"/local/jobs/{job_id}/fund")
            for _ in range(5):
                await engine.tick()
            proof = (await client.get("/evidence", params={"job_id": job_id})).json()
            assert proof["phase"] == "result_submitted" and proof["order_id"].startswith("SIM-")
            assert proof["chain_transactions"] == []
            assert "123 Example St" not in str(proof)
            assert TOKEN not in str(proof)
    finally:
        store.close()
