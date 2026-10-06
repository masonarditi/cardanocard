from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from cardano_card.masumi_adapter import MasumiEscrow


@pytest.fixture
def adapter():
    # Dependency-free fixture; production uses the pinned SDK hash helpers.
    result = object.__new__(MasumiEscrow)
    result.agent_identifier, result.seller_vkey = "registered-agent", "seller"
    result.input_hash, result.output_hash = lambda *_:"input-hash", lambda *_:"output-hash"
    return result


def response():
    return {"blockchainIdentifier":"escrow", "inputHash":"input-hash", "resultHash":"output-hash",
            "payByTime":"2000000000000", "submitResultTime":"2000001200000",
            "unlockTime":"2000002200000", "externalDisputeUnlockTime":"2000003200000",
            "PaymentSource":{"network":"Preprod", "paymentType":"Web3CardanoV1", "smartContractAddress":"addr_test_contract"},
            "SmartContractWallet":{"walletVkey":"seller"}, "RequestedFunds":[{"unit":"", "amount":"10000000"}],
            "onChainState":"FundsLocked", "NextAction":{"requestedAction":"WaitingForExternalAction"}}


async def test_create_preserves_ms_terms_and_checks_seller(adapter):
    adapter.post = AsyncMock(return_value=response())
    result = await adapter.create({"wire_input":{}, "caller_id":"a"*26})
    assert result["payByTime"] == 2000000000
    assert result["rawTimes"]["payByTime"] == "2000000000000"
    payload = adapter.post.call_args.args[1]
    assert payload["network"] == "Preprod" and payload["paymentType"] == "Web3CardanoV1"
    assert payload["payByTime"].endswith("Z")
    data = response()
    data["SmartContractWallet"]["walletVkey"] = "different"
    adapter.post.return_value = data
    with pytest.raises(ValueError, match="identity"):
        await adapter.create({"wire_input":{}, "caller_id":"a"*26})


async def test_observation_checks_datum_and_records_only_valid_tx_hashes(adapter):
    data = response()
    adapter.post = AsyncMock(return_value=data)
    payment = await adapter.create({"wire_input":{}, "caller_id":"a"*26})
    job = {"payment":payment, "caller_id":"a"*26, "result":None}
    data["CurrentTransaction"] = {"txHash":"a"*64}
    data["TransactionHistory"] = [{"txHash":"a"*64}, {"txHash":"invalid"}]
    assert await adapter.observe(job) == "FundsLocked"
    assert len(job["chain_transactions"]) == 1
    assert job["chain_transactions"][0]["verified_on_chain"] is False
    assert adapter.post.call_args.args[1]["includeHistory"] == "true"
    data["inputHash"] = "different"
    assert await adapter.observe(job) == "FundsOrDatumInvalid"


async def test_mismatched_result_hash_cannot_settle(adapter):
    data = response()
    adapter.post = AsyncMock(return_value=data)
    payment = await adapter.create({"wire_input":{}, "caller_id":"a"*26})
    data.update(onChainState="ResultSubmitted", resultHash="wrong")
    job = {"payment":payment, "caller_id":"a"*26, "result":"exact result bytes"}
    assert await adapter.observe(job) == "FundsOrDatumInvalid"
    data["resultHash"] = "output-hash"
    assert await adapter.observe(job) == "ResultSubmitted"
    assert job["result_hash"] == "output-hash"


def test_short_preprod_deadlines_respect_pinned_node_minimums():
    now = datetime(2026,10,6,tzinfo=timezone.utc)
    times = {key:datetime.fromisoformat(value) for key,value in MasumiEscrow.deadlines(now).items()}
    assert (times["submitResultTime"]-now).total_seconds() >= 15*60
    assert (times["submitResultTime"]-times["payByTime"]).total_seconds() >= 5*60
    assert (times["unlockTime"]-times["submitResultTime"]).total_seconds() >= 15*60
    assert (times["externalDisputeUnlockTime"]-times["unlockTime"]).total_seconds() >= 15*60


@pytest.mark.parametrize("funds", [[], [{"unit":"", "amount":"-1"}], [{"unit":"", "amount":"1"}]*2])
def test_invalid_payment_amounts_rejected(funds):
    with pytest.raises(ValueError):
        MasumiEscrow.funds(funds)
