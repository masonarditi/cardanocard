import json
from argparse import Namespace

import httpx
import pytest

from cardano_card import sandbox_run
from cardano_card.agentcard_bridge import AgentCardPurchaser, ReplayTransport
from cardano_card.sandbox_transport import SandboxTransport, private_json
from cardano_card.store import Store
from test_lifecycle import PAYLOAD
from test_agentcard_bridge import INPUT


def test_currency_mapping_uses_matching_authenticated_merchant_market():
    raw = {"cart": {"hash": "hash", "merchant": "retail", "totalCents": 100}}
    catalogue = {"merchants": [{"slug": "retail", "market": {"countries": ["US"], "currency": "USD"}}]}
    result = SandboxTransport.with_currency_evidence(raw, catalogue, 123)
    assert result["cart"]["currency"] == "USD"
    assert result["cart"]["currency_evidence"]["source"] == "AgentCard GET /buy/merchants"
    assert "currency" not in raw["cart"]


@pytest.mark.parametrize("merchants", [[], [{"slug": "other", "market": {"countries": ["US"], "currency": "USD"}}],
    [{"slug": "retail", "market": {"countries": ["US"], "currency": "EUR"}}],
    [{"slug": "retail", "market": {"countries": ["US"], "currency": "USD"}}]*2])
def test_currency_mapping_never_guesses(merchants):
    result = SandboxTransport.with_currency_evidence({"cart": {"merchant": "retail"}}, {"merchants": merchants}, 123)
    assert "currency" not in result["cart"]


def test_explicit_cart_currency_is_not_overwritten():
    result = SandboxTransport.with_currency_evidence({"cart": {"merchant": "retail", "currency": "CAD"}},
        {"merchants": [{"slug": "retail", "market": {"countries": ["US"], "currency": "USD"}}]}, 123)
    assert result["cart"]["currency"] == "CAD"


@pytest.fixture
def credentials(tmp_path):
    (tmp_path / ".env").write_text("AGENTCARD_CLIENT_ID=matching-id\nAGENTCARD_CLIENT_SECRET=matching-secret\n")
    private_json(tmp_path / ".agentcard_tokens.json", dict(user_id="user", access_token="user-token", refresh_token="refresh-token", expires_at=10000))
    return tmp_path


def provider(req, *, sandbox=True):
    if req.url.path == "/api/v2/oauth/token":
        assert b"matching-id" in req.content
        return httpx.Response(200, json={"access_token": "org-token", "expires_in": 3600})
    if req.url.path == "/api/v2":
        assert req.headers["Authorization"] == "Bearer org-token"
        return httpx.Response(200, json={"test_mode": sandbox})
    raise AssertionError("Unexpected user request")


async def test_production_identity_blocks_user_token_refresh_and_checkout(credentials):
    calls = []
    def handle(req):
        calls.append(req.url.path)
        return provider(req, sandbox=False)
    async with httpx.AsyncClient(base_url="https://api.agentcard.sh", transport=httpx.MockTransport(handle)) as client:
        transport = SandboxTransport(credentials, exclusive_until=500, clock=lambda:100, client=client)
        try:
            with pytest.raises(ValueError, match="verification failed"):
                await transport.buy({}, "job")
            assert transport.halted
            assert calls == ["/api/v2/oauth/token", "/api/v2"]
        finally:
            await transport.close()


async def test_refresh_timeout_persists_stop_and_cannot_retry_on_restart(credentials):
    path = credentials / ".agentcard_tokens.json"
    original = path.read_text()
    data = json.loads(original); data["expires_at"] = 1; private_json(path, data)
    calls, lines = [], []
    def handle(req):
        calls.append(req.url.path)
        if req.url.path == "/api/v2/connect/refresh":
            raise httpx.ReadTimeout("private token data")
        return provider(req)
    async with httpx.AsyncClient(base_url="https://api.agentcard.sh", transport=httpx.MockTransport(handle)) as client:
        transport = SandboxTransport(credentials, exclusive_until=500, clock=lambda:100, client=client, emit=lines.append)
        try:
            with pytest.raises(ValueError, match="reconciliation"):
                await transport.buy({}, "job")
            with pytest.raises(ValueError):
                await transport.buy({}, "job")
            assert calls.count("/api/v2/connect/refresh") == 1
            assert "/buy" not in calls
        finally:
            await transport.close()
        with pytest.raises(ValueError, match="unresolved"):
            SandboxTransport(credentials, exclusive_until=500, clock=lambda:100, client=client)
    assert not any("private token" in line for line in lines)


async def test_rotated_tokens_saved_atomically_and_user_rejection_stops(credentials, monkeypatch):
    monkeypatch.setenv("AGENTCARD_CLIENT_ID", "wrong-root-id")
    path = credentials / ".agentcard_tokens.json"
    data = json.loads(path.read_text()); data["expires_at"] = 1; private_json(path, data)
    calls = []
    def handle(req):
        calls.append(req.url.path)
        if req.url.path == "/api/v2/connect/refresh":
            return httpx.Response(200, json={"access_token": "new-user", "refresh_token": "new-refresh", "expires_in": 3600})
        if req.url.path == "/buy":
            assert req.headers["Authorization"] == "Bearer new-user"
            return httpx.Response(401)
        return provider(req)
    async with httpx.AsyncClient(base_url="https://api.agentcard.sh", transport=httpx.MockTransport(handle)) as client:
        transport = SandboxTransport(credentials, exclusive_until=500, clock=lambda:100, client=client, emit=lambda _:None)
        try:
            with pytest.raises(ValueError, match="rejected"):
                await transport.buy({}, "job")
            assert transport.halted
            assert json.loads(path.read_text())["refresh_token"] == "new-refresh"
            assert path.stat().st_mode & 0o777 == 0o600
            assert not (credentials / ".agentcard_refresh_pending").exists()
        finally:
            await transport.close()


def test_handoff_and_local_lock_are_required(credentials):
    with pytest.raises(ValueError, match="handoff"):
        SandboxTransport(credentials, exclusive_until=0)


async def test_exclusive_local_token_lock(credentials):
    transport = SandboxTransport(credentials, exclusive_until=500, clock=lambda:100)
    try:
        with pytest.raises(ValueError, match="owns the token"):
            SandboxTransport(credentials, exclusive_until=500, clock=lambda:100)
    finally:
        await transport.close()


async def test_lost_confirm_recovers_documented_sandbox_decline(tmp_path):
    store = Store(str(tmp_path / "job.db"))
    transport = ReplayTransport(store)
    original = transport.buy
    async def buy(body, request_id):
        if "confirm" in body:
            raise httpx.ReadTimeout("lost")
        return await original(body, request_id)
    async def conversation(cid, request_id):
        return {"conversation_id": cid, "turn_in_progress": False, "orders": [],
                "last_checkout": {"status": "denied", "decline_code": "sandbox_mode", "charge_status": "none"}}
    transport.buy, transport.conversation = buy, conversation
    try:
        bridge = AgentCardPurchaser(store, transport)
        assert (await bridge.purchase(INPUT, "job"))["status"] == "pending"
        assert (await bridge.inspect("job"))["status"] == "failed"
        record = store.get("agentcard_purchases", "job")
        assert record["provider_reason"] == "sandbox_mode" and record["confirm_attempts"] == 1
    finally:
        store.close()


async def test_documented_order_id_is_recovered_without_reconfirmation(tmp_path):
    store = Store(str(tmp_path / "recover.db"))
    transport = ReplayTransport(store, "timeout_recovered")
    async def conversation(cid, request_id):
        return {"conversation_id": cid, "turn_in_progress": False,
                "orders": [{"order_id": "provider-order"}]}
    transport.conversation = conversation
    try:
        bridge = AgentCardPurchaser(store, transport)
        assert (await bridge.purchase(INPUT, "job"))["status"] == "pending"
        assert (await bridge.inspect("job"))["reason"] == "processing"
        assert store.get("agentcard_purchases", "job")["order_id"] == "provider-order"
        assert store.get("agentcard_replay", "job")["confirms"] == 1
    finally:
        store.close()


async def test_sandbox_runner_through_agent_refund_with_mock_http(credentials, monkeypatch, capsys):
    """Integration fixture with explicit currency; not a captured live response."""
    now = __import__('time').time()
    private_json(credentials / ".agentcard_tokens.json", dict(user_id="user", access_token="user-token", refresh_token="refresh-token", expires_at=now+3600))
    confirms = []
    def handle(req):
        if req.url.path == "/buy":
            body = json.loads(req.content)
            if "confirm" in body:
                confirms.append(body)
                return httpx.Response(200, json={"conversation_id": "conv", "status": "declined", "decline_code": "sandbox_mode", "charge_status": "none"})
            return httpx.Response(200, json={"conversation_id": "conv", "cart": {"hash": "cart", "totalCents": 750, "currency": "USD"}})
        return provider(req)
    async with httpx.AsyncClient(base_url="https://api.agentcard.sh", transport=httpx.MockTransport(handle)) as client:
        monkeypatch.setattr(sandbox_run, "SandboxTransport", lambda directory, **kwargs: SandboxTransport(directory, client=client, **kwargs))
        async def no_sleep(_): pass
        monkeypatch.setattr(sandbox_run.asyncio, "sleep", no_sleep)
        request_file = credentials / "input.json"; request_file.write_text(json.dumps(PAYLOAD["input_data"]))
        args = Namespace(agentcard_dir=str(credentials), execute=True, exclusive_handoff=True,
            request=str(request_file), request_id=PAYLOAD["identifier_from_purchaser"], db=str(credentials / "jobs.db"), timeout=30, evidence_dir=str(credentials / "evidence"))
        assert await sandbox_run.run(args) == 0
        assert await sandbox_run.run(args) == 0
    assert len(confirms) == 1
    output = capsys.readouterr().out
    assert "PASS | AgentCard sandbox" in output
    assert "matching-secret" not in output and "user-token" not in output
    evidence = json.loads(next((credentials / "evidence").glob('*.json')).read_text())
    assert evidence["escrow_mode"] == "simulated" and evidence["purchase_mode"] == "external"
    assert evidence["chain_transactions"] == [] and evidence["agentcard"]["provider_reason"] == "sandbox_mode"
