"""Hosted (app.masumi.network, Web3CardanoV2) escrow adapter: offline, shapes from the live hosted OpenAPI."""
import json

import httpx
import pytest

from cardano_card.hosted_escrow import HOSTED_URL, HostedMasumiEscrow

SELLER = "ebef83fc35d6c43f6a69b3d2666d6b8ff27862ce84ee0603d3d7a038"
AGENT = "67ab0c92c4ac1610895a1c965ee50aba41a8f1513b15240723b3bd0b113c4cc308fac5e3056f6ad27c1219443a800f3a3911fb7bc4961c38c6000000"
PAYOUT = "addr_test1qpgq4gf9fmzg3gkdnp9jdtujtscr2qdms07tmtp8aqz5ykc7uvlelkeuqsgctx9n4en6xmkf4qhqkumzcc3qwt4845hqsxwadj"
CONTRACT = "addr_test1wzs4e6wc95hkwezlccjw9mdvq0r0rsgx6zk34avptga3ftgn37w4g"
V1_CONTRACT = "addr_test1wz7j4kmg2cs7yf92uat3ed4a3u97kr7axxr4avaz0lhwdsqukgwfm"


def adapter():
    result = object.__new__(HostedMasumiEscrow)
    result.input_hash, result.output_hash = lambda *_: "input-hash", lambda *_: "output-hash"
    result.url, result.api_key, result.agent_identifier, result.seller_vkey = HOSTED_URL, "mas_test", AGENT, SELLER
    result.payout_address, result.fee_lovelace, result.contract, result.source_index = PAYOUT, "10000000", None, 0
    result.lovelace_per_usd, result.fixed_price = None, False
    return result


def sources():
    return {"PaymentSources": [
        {"network": "Preprod", "paymentSourceType": "Web3CardanoV2", "smartContractAddress": CONTRACT},
        {"network": "Preprod", "paymentSourceType": "Web3CardanoV1", "smartContractAddress": V1_CONTRACT},
        {"network": "Mainnet", "paymentSourceType": "Web3CardanoV2", "smartContractAddress": "addr1wxs4e6wc95hkwezlccjw9mdvq0r0rsgx6zk34avptga3ftgge2j6d"}]}


def payment(**over):
    return {"blockchainIdentifier": "bid-1", "inputHash": "input-hash", "agentIdentifier": AGENT, "resultHash": None,
            "payByTime": "2000000000000", "submitResultTime": "2000001200000", "unlockTime": "2000002200000",
            "externalDisputeUnlockTime": "2000003200000", "sellerReturnAddress": PAYOUT,
            "PaymentSource": {"network": "Preprod", "paymentSourceType": "Web3CardanoV2", "smartContractAddress": CONTRACT},
            "SmartContractWallet": {"walletVkey": SELLER, "walletAddress": "addr_test1..."},
            "RequestedFunds": [{"unit": "", "amount": "10000000"}], "onChainState": None,
            "NextAction": {"requestedAction": "WaitingForExternalAction"}, **over}


class Hosted:
    def __init__(self):
        self.payment, self.calls, self.sources = payment(), [], sources()

    def handler(self, request):
        assert str(request.url).startswith(HOSTED_URL) and request.headers["x-api-key"] == "mas_test"
        assert "token" not in request.headers
        route = str(request.url)[len(HOSTED_URL):].split("?")[0]
        body = json.loads(request.content) if request.content else None
        self.calls.append((request.method, route, body))
        if route == "payment-source":
            return httpx.Response(200, json={"status": "success", "data": self.sources})
        if route in ("payment", "payment/resolve-blockchain-identifier", "payment/submit-result", "payment/authorize-refund"):
            return httpx.Response(200, json={"status": "success", "data": self.payment})
        return httpx.Response(404, json={"status": "error"})


@pytest.fixture
def hosted(monkeypatch):
    fake = Hosted()
    real = httpx.AsyncClient

    def client(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(fake.handler)
        return real(*args, **kwargs)
    monkeypatch.setattr(httpx, "AsyncClient", client)
    return fake


async def test_create_sends_v2_dynamic_fee_and_payout_binding(hosted):
    escrow = adapter()
    terms = await escrow.create({"wire_input": {"ask": "gum"}, "caller_id": "a" * 26})
    method, route, body = hosted.calls[-1]
    assert (method, route) == ("POST", "payment")
    assert body["paymentSourceType"] == "Web3CardanoV2" and body["agentIdentifier"] == AGENT
    assert body["RequestedFunds"] == [{"unit": "", "amount": "10000000"}] and body["sellerReturnAddress"] == PAYOUT
    assert body["payByTime"].endswith("Z") and "paymentType" not in body and body["supportedPaymentSourceIndex"] == 0
    assert terms["payByTime"] == 2000000000 and terms["payoutAddress"] == PAYOUT and terms["rail"] == "hosted-v2"
    assert terms["smartContractAddress"] == CONTRACT and terms["sellerVKey"] == SELLER


@pytest.mark.parametrize("mutation", ["seller", "source_type", "contract", "fee", "payout", "hash"])
async def test_create_rejects_identity_drift(hosted, mutation):
    escrow = adapter()
    p = hosted.payment
    if mutation == "seller":
        p["SmartContractWallet"]["walletVkey"] = "0" * 56
    elif mutation == "source_type":
        p["PaymentSource"]["paymentSourceType"] = "Web3CardanoV1"
    elif mutation == "contract":
        p["PaymentSource"]["smartContractAddress"] = V1_CONTRACT
    elif mutation == "fee":
        p["RequestedFunds"] = [{"unit": "", "amount": "9000000"}]
    elif mutation == "payout":
        p["sellerReturnAddress"] = V1_CONTRACT
    else:
        p["inputHash"] = "other"
    with pytest.raises(ValueError):
        await escrow.create({"wire_input": {}, "caller_id": "a" * 26})


async def test_observe_maps_v2_states_and_records_hashes(hosted):
    escrow = adapter()
    terms = await escrow.create({"wire_input": {}, "caller_id": "a" * 26})
    job = {"payment": terms, "caller_id": "a" * 26, "result": None}
    hosted.payment.update(onChainState="FundsLocked", CurrentTransaction={"txHash": "b" * 64},
                          TransactionHistory=[{"txHash": "a" * 64}, {"txHash": "nope"}])
    assert await escrow.observe(job) == "FundsLocked"
    assert sorted(t["tx_hash"][0] for t in job["chain_transactions"]) == ["a", "b"]
    assert job["chain_transactions"][0]["reported_by"] == "masumi-hosted"
    hosted.payment.update(onChainState="WithdrawAuthorized", resultHash="output-hash")
    job["result"] = "{}"
    assert await escrow.observe(job) == "ResultSubmitted" and job["result_hash"] == "output-hash"
    hosted.payment.update(resultHash="tampered")
    assert await escrow.observe(job) == "FundsOrDatumInvalid"
    hosted.payment.update(resultHash="output-hash", onChainState="RefundAuthorized")
    assert await escrow.observe(job) == "RefundRequested"


async def test_observe_fails_closed_on_rail_or_source_change(hosted):
    escrow = adapter()
    terms = await escrow.create({"wire_input": {}, "caller_id": "a" * 26})
    job = {"payment": terms, "caller_id": "a" * 26, "result": None}
    hosted.payment["PaymentSource"]["smartContractAddress"] = V1_CONTRACT
    assert await escrow.observe(job) == "FundsOrDatumInvalid"
    hosted.payment["PaymentSource"]["smartContractAddress"] = CONTRACT
    hosted.payment["RequestedFunds"] = [{"unit": "", "amount": "1"}]
    assert await escrow.observe(job) == "FundsOrDatumInvalid"


async def test_submit_and_refund_use_hosted_routes(hosted):
    escrow = adapter()
    terms = await escrow.create({"wire_input": {}, "caller_id": "a" * 26})
    job = {"payment": terms, "caller_id": "a" * 26, "result": "{}"}
    await escrow.submit(job, "{}")
    assert hosted.calls[-1][1:] == ("payment/submit-result", {"network": "Preprod", "blockchainIdentifier": "bid-1",
                                                               "submitResultHash": "output-hash"})
    await escrow.authorize_refund(job)
    assert hosted.calls[-1][1] == "payment/authorize-refund"
    assert escrow.automatic_requested_refund is False


async def test_missing_or_ambiguous_v2_source_blocks_creation(hosted):
    escrow = adapter()
    hosted.sources = {"PaymentSources": sources()["PaymentSources"][1:]}  # V1 + mainnet only
    with pytest.raises(ValueError, match="missing or ambiguous"):
        await escrow.create({"wire_input": {}, "caller_id": "a" * 26})
    assert not [c for c in hosted.calls if c[1] == "payment"]


@pytest.mark.parametrize("kwargs", [
    {"url": "https://evil.example/pay/api/v1/"}, {"url": "http://app.masumi.network/pay/api/v1/"},
    {"seller_vkey": "not-hex"}, {"payout_address": "addr1qmainnet"}, {"fee_lovelace": "0"}, {"api_key": ""}])
def test_constructor_pins_host_and_identity(kwargs):
    base = {"url": HOSTED_URL, "api_key": "mas_x", "agent_identifier": AGENT, "seller_vkey": SELLER,
            "payout_address": PAYOUT, "fee_lovelace": "10000000"}
    with pytest.raises(ValueError):
        HostedMasumiEscrow(**{**base, **kwargs})


@pytest.mark.parametrize("budget,rate,expected", [
    ("8.50", 1_000_000, "10000000"),    # below the floor -> floor (10 tADA)
    ("12.34", 1_000_000, "12340000"),   # 12.34 tADA
    ("12.345", 1_000_000, "12345000"),
    ("7.00", 2_500_000, "17500000"),    # 2.5 tADA per USD
    ("9999.00", 1_000_000, "100000000"),  # capped at 100 tADA
])
async def test_dynamic_escrow_follows_the_buyer_budget(hosted, budget, rate, expected):
    escrow = adapter()
    escrow.lovelace_per_usd = rate
    hosted.payment["RequestedFunds"] = [{"unit": "", "amount": expected}]
    terms = await escrow.create({"wire_input": {}, "caller_id": "a" * 26, "input": {"max_total_usd": budget}})
    assert hosted.calls[-1][2]["RequestedFunds"] == [{"unit": "", "amount": expected}]
    assert terms["RequestedFunds"] == [{"unit": "", "amount": expected}]


async def test_service_cannot_change_the_dynamic_amount(hosted):
    escrow = adapter()
    escrow.lovelace_per_usd = 1_000_000
    hosted.payment["RequestedFunds"] = [{"unit": "", "amount": "10000000"}]  # service echoes a different amount
    with pytest.raises(ValueError, match="escrow amount"):
        await escrow.create({"wire_input": {}, "caller_id": "a" * 26, "input": {"max_total_usd": "20.00"}})


async def test_fixed_price_agent_omits_requested_funds_and_accepts_registry_price(hosted):
    escrow = adapter()
    escrow.fixed_price = True
    hosted.payment["RequestedFunds"] = [{"unit": "", "amount": "10000000"}]
    terms = await escrow.create({"wire_input": {}, "caller_id": "a" * 26, "input": {"max_total_usd": "99.00"}})
    assert "RequestedFunds" not in hosted.calls[-1][2]
    assert terms["RequestedFunds"] == [{"unit": "", "amount": "10000000"}]
    hosted.payment["RequestedFunds"] = [{"unit": "", "amount": "10000000"}, {"unit": "abc", "amount": "1"}]
    with pytest.raises(ValueError, match="Fixed-price"):
        await escrow.create({"wire_input": {}, "caller_id": "b" * 26, "input": {"max_total_usd": "1.00"}})


def test_constructor_accepts_fixed_mode():
    e = HostedMasumiEscrow(HOSTED_URL, "mas_x", AGENT, SELLER, PAYOUT, "10000000", lovelace_per_usd="fixed")
    assert e.fixed_price is True and e.lovelace_per_usd is None
