import asyncio
import copy

import httpx
import pytest

from cardano_card.preprod_buyer import PreprodBuyer, loopback_url
from cardano_card.store import Store
from test_lifecycle import PAYLOAD


def terms():
    return {"id": "job", "simulated_escrow": False, "blockchainIdentifier": "escrow-id",
            "agentIdentifier": "expected-agent", "sellerVKey": "expected-seller", "inputHash": "expected-hash",
            "RequestedFunds": [{"unit":"", "amount":"10000000"}],
            "rawTimes": {"payByTime": "2000000000000", "submitResultTime": "2000001000000",
                         "unlockTime": "2000002000000", "externalDisputeUnlockTime": "2000003000000"}}


@pytest.fixture
def buyer(tmp_path):
    store = Store(str(tmp_path / "buyer.db"))
    class Client:
        def __init__(self):
            self.calls = []
        async def post(self, route, json):
            self.calls.append((route, json))
            return httpx.Response(200, json={"status": "success", "data": {}}, request=httpx.Request("POST", "http://localhost" + route))
    result = PreprodBuyer(store, Client(), "expected-agent", "expected-seller", lambda *_: "expected-hash", "buyer-identity", clock=lambda: 1900000000,
                          expected_funds=[{"unit":"", "amount":"10000000"}])
    yield result
    store.close()


async def test_v1_funding_preserves_raw_times_and_never_repeats(buyer):
    results = await asyncio.gather(*(buyer.fund(PAYLOAD, terms()) for _ in range(4)))
    assert all(r["state"] == "accepted_by_node" for r in results)
    assert len(buyer.client.calls) == 1
    route, payload = buyer.client.calls[0]
    assert route == "/purchase/"
    assert payload["network"] == "Preprod" and payload["paymentType"] == "Web3CardanoV1"
    assert payload["sellerVkey"] == "expected-seller"
    assert payload["payByTime"] == "2000000000000"
    assert payload["inputHash"] == "expected-hash"


@pytest.mark.parametrize("field,value", [("sellerVKey", "wrong"), ("agentIdentifier", "wrong"),
    ("inputHash", "wrong"), ("simulated_escrow", True), ("blockchainIdentifier", "SIM-fake")])
async def test_invalid_payment_identity_rejected_before_write(buyer, field, value):
    t = terms()
    t[field] = value
    with pytest.raises(ValueError):
        await buyer.fund(PAYLOAD, t)
    assert not buyer.client.calls


async def test_expired_and_mixed_deadlines_rejected(buyer):
    t = terms()
    t["rawTimes"]["payByTime"] = "1800000000000"
    with pytest.raises(ValueError, match="deadline"):
        await buyer.fund(PAYLOAD, t)
    t["rawTimes"]["payByTime"] = "2000000000"
    with pytest.raises(ValueError, match="Mixed"):
        await buyer.fund(PAYLOAD, t)
    assert not buyer.client.calls


async def test_fee_above_fixed_test_budget_is_rejected(buyer):
    t = terms()
    t["RequestedFunds"][0]["amount"] = "10000001"
    with pytest.raises(ValueError, match="Service fee"):
        await buyer.fund(PAYLOAD, t)
    assert not buyer.client.calls


async def test_unknown_funding_stays_unknown_after_restart(buyer):
    async def lost(*args, **kwargs):
        raise TimeoutError()
    buyer.client.post = lost
    assert (await buyer.fund(PAYLOAD, terms()))["state"] == "unknown"
    replacement = PreprodBuyer(buyer.store, buyer.client, "expected-agent", "expected-seller", lambda *_: "expected-hash", "buyer-identity")
    assert (await replacement.fund(PAYLOAD, terms()))["state"] == "unknown"
    changed = copy.deepcopy(PAYLOAD)
    changed["input_data"]["ask"] = "different"
    with pytest.raises(ValueError, match="changed"):
        await replacement.fund(changed, terms())


async def test_refund_requires_known_job_and_definitive_failure(buyer):
    await buyer.fund(PAYLOAD, terms())
    status = {"id": "job", "simulated_escrow": False, "phase": "reconciling", "purchase": {"status": "pending"}}
    with pytest.raises(ValueError, match="definitive"):
        await buyer.refund("job", status)
    status.update(phase="refund_due", purchase={"status": "failed", "reason": "declined"})
    await buyer.refund("job", status)
    await buyer.refund("job", status)
    assert len(buyer.client.calls) == 2
    assert buyer.client.calls[1] == ("/purchase/request-refund", {"network": "Preprod", "blockchainIdentifier": "escrow-id"})


@pytest.mark.parametrize("url", ["https://example.com", "http://user:secret@localhost", "http://localhost/?key=secret", "http://localhost/#fragment"])
def test_buyer_rejects_nonlocal_or_credential_urls(url):
    with pytest.raises(ValueError):
        loopback_url(url)


async def test_fixed_price_wire_contract_omits_amounts(buyer):
    async def fixed_price_node(route, json):
        # Pinned Masumi V1 rejects this property even when the amount is correct.
        status = 400 if 'Amounts' in json else 200
        return httpx.Response(status, json={'status': 'error' if status == 400 else 'success'},
                              request=httpx.Request('POST', 'http://localhost/purchase/'))
    buyer.client.post = fixed_price_node
    assert (await buyer.fund(PAYLOAD, terms()))['state'] == 'accepted_by_node'


@pytest.mark.parametrize('status', [400, 401, 409, 500])
async def test_http_failure_is_durable_private_and_never_retried(buyer, status):
    calls = []
    async def rejected(route, json):
        calls.append(route)
        return httpx.Response(status, json={'secret': 'must-not-be-persisted'},
                              request=httpx.Request('POST', 'http://localhost/purchase/'))
    buyer.client.post = rejected
    first = await buyer.fund(PAYLOAD, terms())
    assert first['state'] == 'unknown'
    replacement = PreprodBuyer(buyer.store, buyer.client, 'expected-agent', 'expected-seller',
                               lambda *_: 'expected-hash', 'buyer-identity')
    assert await replacement.fund(PAYLOAD, terms()) == first
    assert calls == ['/purchase/']
    assert 'must-not-be-persisted' not in str(buyer.store.get('buyer_writes', 'fund:job'))


def test_expected_funds_flag_parses_ada_and_tokens(monkeypatch):
    from argparse import Namespace
    from cardano_card.preprod_buyer import expected_funds
    monkeypatch.setenv("MASUMI_FEE_LOVELACE", "10000000")
    assert expected_funds(Namespace(expected_funds=None)) == {"unit": "", "amount": "10000000"}
    assert expected_funds(Namespace(expected_funds="lovelace:5000000")) == {"unit": "", "amount": "5000000"}
    assert expected_funds(Namespace(expected_funds="16a55b2a" + "0" * 48 + "0014df10745553444d:20000000")) == {"unit": "16a55b2a" + "0" * 48 + "0014df10745553444d", "amount": "20000000"}
    with pytest.raises(ValueError):
        expected_funds(Namespace(expected_funds="abc:zero"))
