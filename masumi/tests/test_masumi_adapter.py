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


SIGNING_ADDRESS = 'addr_test1qp30fkp0fs40zhmukxm633nkfy5lf02s9a5yjdwx64ypw0p94zyl0q93enhe2nu4h76e0qa4sc3n2r24jl33pu2zlvvqevvhcw'
SIGNING_KEY = '62f4d82f4c2af15f7cb1b7a8c6764929f4bd502f684935c6d548173c'
COLLECTION_ADDRESS = 'addr_test1qrgpga399l0r8fg7n0jfshhxsjl26w0uslxf5m02yclur8mremst4rk8xsz9lx78e9sdtjfsyj3c9kll2c4958uhkals2qrm9q'
CONTRACT_ADDRESS = 'addr_test1wz7j4kmg2cs7yf92uat3ed4a3u97kr7axxr4avaz0lhwdsqukgwfm'


def payout_sources():
    return {'PaymentSources': [{'id': 'source1', 'network': 'Preprod', 'paymentType': 'Web3CardanoV1',
        'smartContractAddress': CONTRACT_ADDRESS,
        'SellingWallets': [{'walletVkey': SIGNING_KEY, 'walletAddress': SIGNING_ADDRESS,
                            'collectionAddress': COLLECTION_ADDRESS}]}]}


@pytest.fixture
def routed(adapter):
    adapter.seller_vkey = SIGNING_KEY
    adapter.payout_address = COLLECTION_ADDRESS
    data = response()
    data['PaymentSource']['smartContractAddress'] = CONTRACT_ADDRESS
    data['SmartContractWallet']['walletVkey'] = SIGNING_KEY
    adapter.get = AsyncMock(return_value=payout_sources())
    adapter.post = AsyncMock(return_value=data)
    return adapter


async def create_routed(adapter):
    return await adapter.create({'wire_input': {}, 'caller_id': 'a'*26})


async def test_routing_checked_before_create_and_recipient_saved(routed):
    order = []
    async def sources(*args, **kwargs):
        order.append('read-routing')
        return payout_sources()
    response_data = routed.post.return_value
    async def post(*args, **kwargs):
        order.append('create-payment')
        return response_data
    routed.get.side_effect, routed.post.side_effect = sources, post
    payment = await create_routed(routed)
    assert order == ['read-routing', 'create-payment']
    assert payment['payoutAddress'] == COLLECTION_ADDRESS
    assert payment['sellerVKey'] == SIGNING_KEY
    assert payment['smartContractAddress'] == CONTRACT_ADDRESS
    routed.get.assert_awaited_once_with('payment-source/', {'take': 100})


@pytest.mark.parametrize('collection', [None, ''])
async def test_empty_collection_falls_back_to_signing_wallet(routed, collection):
    routed.payout_address = SIGNING_ADDRESS
    routed.get.return_value['PaymentSources'][0]['SellingWallets'][0]['collectionAddress'] = collection
    payment = await create_routed(routed)
    assert payment['payoutAddress'] == SIGNING_ADDRESS


async def test_wrong_recipient_prevents_payment_creation(routed):
    routed.get.return_value['PaymentSources'][0]['SellingWallets'][0]['collectionAddress'] = SIGNING_ADDRESS
    with pytest.raises(ValueError, match='collection address'):
        await create_routed(routed)
    routed.post.assert_not_awaited()


@pytest.mark.parametrize('mutation', ['network', 'type', 'duplicate_source', 'duplicate_wallet',
                                     'wrong_key', 'signer_key_mismatch', 'wrong_contract_type',
                                     'malformed', 'full_page', 'missing'])
async def test_ambiguous_missing_or_invalid_source_blocks_creation(routed, mutation):
    import copy
    source = routed.get.return_value['PaymentSources'][0]
    if mutation == 'network':
        source['network'] = 'Mainnet'
    elif mutation == 'type':
        source['paymentType'] = 'Web3CardanoV2'
    elif mutation == 'duplicate_source':
        routed.get.return_value['PaymentSources'].append(copy.deepcopy(source))
    elif mutation == 'duplicate_wallet':
        source['SellingWallets'].append(copy.deepcopy(source['SellingWallets'][0]))
    elif mutation == 'wrong_key':
        source['SellingWallets'][0]['walletVkey'] = 'a'*56
    elif mutation == 'signer_key_mismatch':
        source['SellingWallets'][0]['walletAddress'] = COLLECTION_ADDRESS
    elif mutation == 'wrong_contract_type':
        source['smartContractAddress'] = SIGNING_ADDRESS
    elif mutation == 'malformed':
        source['SellingWallets'] = None
    elif mutation == 'full_page':
        routed.get.return_value['PaymentSources'] = [copy.deepcopy(source) for _ in range(100)]
    else:
        routed.get.return_value['PaymentSources'] = []
    with pytest.raises(ValueError):
        await create_routed(routed)
    routed.post.assert_not_awaited()


async def test_created_payment_cannot_switch_contract_after_route_check(routed):
    routed.post.return_value['PaymentSource']['smartContractAddress'] = 'different-contract'
    with pytest.raises(ValueError, match='identity'):
        await create_routed(routed)


async def test_changed_route_blocks_observation_before_checkout(routed):
    payment = await create_routed(routed)
    routed.post.reset_mock()
    routed.get.return_value['PaymentSources'][0]['SellingWallets'][0]['collectionAddress'] = SIGNING_ADDRESS
    assert await routed.observe({'payment': payment}) == 'FundsOrDatumInvalid'
    routed.post.assert_not_awaited()


async def test_changed_route_blocks_result_submission(routed):
    payment = await create_routed(routed)
    routed.post.reset_mock()
    routed.get.return_value['PaymentSources'][0]['SellingWallets'][0]['collectionAddress'] = SIGNING_ADDRESS
    with pytest.raises(ValueError, match='collection address'):
        await routed.submit({'payment': payment, 'caller_id': 'a'*26}, 'verified result')
    routed.post.assert_not_awaited()


async def test_saved_snapshot_is_enforced_without_current_payout_config(routed):
    payment = await create_routed(routed)
    routed.payout_address = None
    job = {'payment': payment, 'caller_id': 'a'*26}
    assert await routed.observe(job) == 'FundsLocked'
    routed.get.return_value['PaymentSources'][0]['SellingWallets'][0]['collectionAddress'] = SIGNING_ADDRESS
    assert await routed.observe(job) == 'FundsOrDatumInvalid'
    with pytest.raises(ValueError):
        await routed.submit(job, 'verified result')


async def test_configured_recipient_cannot_override_saved_snapshot(routed):
    payment = await create_routed(routed)
    routed.payout_address = SIGNING_ADDRESS
    routed.get.reset_mock()
    with pytest.raises(ValueError, match='saved payment terms'):
        await routed.validate_payout(payment)
    routed.get.assert_not_awaited()


async def test_saved_payment_contract_and_seller_are_rechecked(routed):
    payment = await create_routed(routed)
    payment['smartContractAddress'] = SIGNING_ADDRESS
    with pytest.raises(ValueError, match='missing or ambiguous'):
        await routed.validate_payout(payment)
    payment['sellerVKey'] = 'a'*56
    routed.get.reset_mock()
    with pytest.raises(ValueError, match='identity'):
        await routed.validate_payout(payment)
    routed.get.assert_not_awaited()


async def test_unavailable_route_cannot_submit(routed):
    import httpx
    payment = await create_routed(routed)
    routed.post.reset_mock()
    routed.get.side_effect = httpx.ConnectTimeout('unavailable')
    with pytest.raises(httpx.ConnectTimeout):
        await routed.submit({'payment': payment, 'caller_id': 'a'*26}, 'verified result')
    routed.post.assert_not_awaited()


async def test_unchanged_route_permits_submit_and_explicit_checkout_recheck(routed):
    payment = await create_routed(routed)
    routed.get.reset_mock()
    routed.post.reset_mock()
    assert await routed.validate_payout(payment) == {'payoutAddress': COLLECTION_ADDRESS,
                                                     'smartContractAddress': CONTRACT_ADDRESS}
    await routed.submit({'payment': payment, 'caller_id': 'a'*26}, 'verified result')
    assert routed.get.await_count == 2
    routed.post.assert_awaited_once_with('payment/submit-result', {'network': 'Preprod',
        'blockchainIdentifier': 'escrow', 'submitResultHash': 'output-hash'})


async def test_legacy_unconfigured_adapter_does_not_query_routing(adapter):
    adapter.get = AsyncMock()
    assert await adapter.validate_payout() is None
    adapter.get.assert_not_awaited()


@pytest.mark.parametrize('address', ['addr1mainnet', COLLECTION_ADDRESS[:-1]+'p', ''])
def test_constructor_rejects_invalid_configured_payout_before_sdk_load(address):
    with pytest.raises(ValueError):
        MasumiEscrow('http://127.0.0.1:3001/api/v1', 'api-key', 'agent-id', SIGNING_KEY,
                     payout_address=address)
