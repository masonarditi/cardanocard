import httpx
import pytest

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
    with pytest.raises(ValueError, match="modes are supported"):
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


def test_production_card_requires_real_escrow(monkeypatch, tmp_path):
    import pytest
    from cardano_card.api import configured_engine
    monkeypatch.setenv("CARDANO_CARD_MODE", "local")
    monkeypatch.setenv("PURCHASE_BACKEND", "mason")
    monkeypatch.setenv("AGENTCARD_ENV", "prod")
    monkeypatch.setenv("CARDANO_CARD_DB", str(tmp_path / "jobs.db"))
    with pytest.raises(ValueError, match="Preprod escrow"):
        configured_engine()


def test_hosted_mode_builds_hosted_escrow_without_local_node(monkeypatch, tmp_path):
    from cardano_card.api import configured_engine
    from cardano_card.hosted_escrow import HOSTED_URL, HostedMasumiEscrow
    monkeypatch.setenv("CARDANO_CARD_MODE", "hosted")
    monkeypatch.setenv("PURCHASE_BACKEND", "fake")
    monkeypatch.setenv("CARDANO_CARD_DB", str(tmp_path / "jobs.db"))
    monkeypatch.setenv("PAYMENT_SERVICE_URL", HOSTED_URL)
    monkeypatch.setenv("PAYMENT_API_KEY", "mas_test")
    monkeypatch.setenv("AGENT_IDENTIFIER", "67ab0c92" + "0" * 108)
    monkeypatch.setenv("SELLER_VKEY", "ebef83fc35d6c43f6a69b3d2666d6b8ff27862ce84ee0603d3d7a038")
    monkeypatch.setenv("PAYOUT_ADDRESS", "addr_test1qpgq4gf9fmzg3gkdnp9jdtujtscr2qdms07tmtp8aqz5ykc7uvlelkeuqsgctx9n4en6xmkf4qhqkumzcc3qwt4845hqsxwadj")
    monkeypatch.delenv("MASUMI_V1_COMPATIBLE", raising=False)
    engine = configured_engine()
    try:
        assert isinstance(engine.escrow, HostedMasumiEscrow) and engine.escrow.simulated is False
    finally:
        engine.store.close()


async def test_public_jobs_open_mip003_routes_but_keep_operator_routes_gated(tmp_path):
    from cardano_card.api import create_app
    from cardano_card.engine import Engine
    from cardano_card.providers import FakeEscrow, FakePurchaser
    from cardano_card.store import Store
    store = Store(str(tmp_path / "jobs.db"))
    engine = Engine(store, FakeEscrow(store), FakePurchaser(store))
    app = create_app(engine=engine, token="t" * 32, background=False, frontend=False, public_jobs=True)
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            async with app.router.lifespan_context(app):
                payload = {"identifier_from_purchaser": "c" * 26, "input_data": {
                    "ask": "Buy a sample", "max_total_usd": "10.00", "street": "1 St", "city": "X", "state": "CA",
                    "zip": "00000", "phone": "+12025550123", "name": "Demo"}}
                started = await client.post("/start_job", json=payload)
                assert started.status_code == 200, started.text
                assert (await client.get("/status", params={"job_id": started.json()["id"]})).status_code == 200
                assert (await client.get("/jobs")).status_code == 401
                assert (await client.get("/evidence", params={"job_id": started.json()["id"]})).status_code == 401
    finally:
        store.close()


async def test_masumi_verification_endpoint_returns_hmac_of_challenge(tmp_path, monkeypatch):
    import hashlib, hmac
    from cardano_card.api import create_app
    from cardano_card.engine import Engine
    from cardano_card.providers import FakeEscrow, FakePurchaser
    from cardano_card.store import Store
    store = Store(str(tmp_path / "jobs.db"))
    app = create_app(engine=Engine(store, FakeEscrow(store), FakePurchaser(store)), token="t" * 32, background=False, frontend=False)
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            async with app.router.lifespan_context(app):
                monkeypatch.delenv("MASUMI_VERIFICATION_SECRET", raising=False)
                assert (await client.get("/get-credential", params={"masumi_challenge": "c"})).status_code == 404
                monkeypatch.setenv("MASUMI_VERIFICATION_SECRET", "s3cret")
                r = await client.get("/get-credential", params={"masumi_challenge": "abc-123"})
                assert r.status_code == 200 and r.headers["content-type"].startswith("text/plain")
                assert r.text == hmac.new(b"s3cret", b"abc-123", hashlib.sha256).hexdigest()
    finally:
        store.close()


async def test_path_prefix_serves_the_same_app(tmp_path):
    from cardano_card.api import PathPrefixes, create_app
    from cardano_card.engine import Engine
    from cardano_card.providers import FakeEscrow, FakePurchaser
    from cardano_card.store import Store
    store = Store(str(tmp_path / "jobs.db"))
    inner = create_app(engine=Engine(store, FakeEscrow(store), FakePurchaser(store)), token="t" * 32, background=False, frontend=False)
    app = PathPrefixes(inner, ["/v2"])
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            async with inner.router.lifespan_context(inner):
                plain = (await client.get("/availability")).json()
                prefixed = (await client.get("/v2/availability")).json()
                assert plain == prefixed and prefixed["status"] == "available"
                assert (await client.get("/v2/input_schema")).status_code == 200
                assert (await client.get("/v2")).status_code in (200, 404)  # bare prefix maps to "/"
                assert (await client.get("/v2jobs")).status_code == 404  # no partial-prefix matches
    finally:
        store.close()


async def test_start_job_response_matches_sokosumi_paid_schema(tmp_path):
    from cardano_card.api import create_app
    from cardano_card.engine import Engine
    from cardano_card.providers import FakeEscrow, FakePurchaser
    from cardano_card.store import Store
    store = Store(str(tmp_path / "jobs.db"))
    app = create_app(engine=Engine(store, FakeEscrow(store), FakePurchaser(store)), token="t" * 32, background=False, frontend=False, public_jobs=True)
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            async with app.router.lifespan_context(app):
                payload = {"identifier_from_purchaser": "d" * 26, "input_data": {
                    "ask": "Buy a sample", "max_total_usd": "10.00", "street": "1 St", "city": "X", "state": "CA",
                    "zip": "00000", "phone": "+12025550123", "name": "Demo"}}
                d = (await client.post("/start_job", json=payload)).json()
                # Sokosumi (packages/masumi/src/schemas/agent/start_job.schema.ts) requires these names/types
                for key in ("id", "input_hash", "identifierFromPurchaser", "blockchainIdentifier", "payByTime",
                            "submitResultTime", "unlockTime", "externalDisputeUnlockTime", "agentIdentifier", "sellerVKey"):
                    assert d.get(key) not in (None, ""), key
                assert all(isinstance(d[k], int) for k in ("payByTime", "submitResultTime", "unlockTime", "externalDisputeUnlockTime"))
                assert d["input_hash"] == d["inputHash"] and 0 <= d["supportedPaymentSourceIndex"] <= 24
                diag = await client.get("/diagnostics", headers={"Authorization": "Bearer " + "t" * 32})
                assert diag.status_code == 200 and diag.json()["jobs"] == 1
                assert (await client.get("/diagnostics")).status_code == 401
    finally:
        store.close()


@pytest.mark.parametrize("ident,ok", [("a" * 14, True), ("b" * 26, True), ("c" * 13, False), ("d" * 27, False), ("G" * 20, False)])
def test_purchaser_identifier_accepts_masumi_hex_nonce_range(ident, ok):
    from pydantic import ValidationError
    from cardano_card.models import StartRequest
    data = {"identifier_from_purchaser": ident, "input_data": {"ask": "x", "max_total_usd": "1.00", "street": "1 St",
            "city": "X", "state": "CA", "zip": "00000", "phone": "+12025550123", "name": "Demo"}}
    if ok:
        StartRequest.model_validate(data)
    else:
        with pytest.raises(ValidationError):
            StartRequest.model_validate(data)


async def test_operator_job_route_is_gated_and_returns_the_stored_job(tmp_path):
    from cardano_card.api import create_app
    from cardano_card.engine import Engine
    from cardano_card.providers import FakeEscrow, FakePurchaser
    from cardano_card.store import Store
    store = Store(str(tmp_path / "jobs.db"))
    app = create_app(engine=Engine(store, FakeEscrow(store), FakePurchaser(store)), token="t" * 32, background=False, frontend=False, public_jobs=True)
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            async with app.router.lifespan_context(app):
                payload = {"identifier_from_purchaser": "e" * 26, "input_data": {
                    "ask": "Buy a sample", "max_total_usd": "10.00", "street": "1 St", "city": "X", "state": "CA",
                    "zip": "00000", "phone": "+12025550123", "name": "Demo"}}
                job_id = (await client.post("/start_job", json=payload)).json()["id"]
                assert (await client.get(f"/operator/jobs/{job_id}")).status_code == 401
                full = (await client.get(f"/operator/jobs/{job_id}", headers={"Authorization": "Bearer " + "t" * 32})).json()
                assert full["id"] == job_id and "payment" in full and "wire_input" in full
                assert (await client.get("/operator/jobs/nope", headers={"Authorization": "Bearer " + "t" * 32})).status_code == 404
    finally:
        store.close()


@pytest.mark.parametrize("raw,expected", [(12.345, "12.35"), (0.1 + 0.2, "0.30"), (20, "20.00"), ("12.5", "12.50"), (12.34, "12.34")])
def test_budget_from_js_numbers_rounds_to_cents(raw, expected):
    from decimal import Decimal
    from cardano_card.models import parse_purchase_input
    base = {"ask": "gum", "street": "1 St", "city": "SF", "state": "CA", "zip": "94123", "phone": "+14155550100", "name": "Demo"}
    assert parse_purchase_input({**base, "max_total_usd": raw}).max_total_usd == Decimal(expected)


def test_input_schema_matches_sokosumi_field_schemas():
    from cardano_card.models import INPUT_SCHEMA
    allowed = {"string": {"placeholder", "description"}, "number": {"default", "placeholder", "description"},
               "text": {"default", "placeholder", "description"}}
    ids = [f["id"] for f in INPUT_SCHEMA["input_data"]]
    assert ids == ["ask", "max_total_usd", "street", "city", "state", "zip", "phone", "name"]
    for field in INPUT_SCHEMA["input_data"]:
        assert field["type"] in allowed and field["name"]
        assert set((field.get("data") or {}).keys()) <= allowed[field["type"]]
