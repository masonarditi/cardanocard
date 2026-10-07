import copy

import httpx
import pytest

from cardano_card.chain_evidence import BlockfrostEvidence, NOWNODES_URL, PREPROD_URL, chain_credential_ok, chain_provider

TX, TX2, BLOCK, DATUM = 'a' * 64, 'b' * 64, 'c' * 64, 'd' * 64
BUYER, SELLER = 'e' * 56, 'f' * 56
CONTRACT, BUYER_ADDRESS, SELLER_ADDRESS = 'addr_test1contract', 'addr_test1buyer', 'addr_test1seller'


def constructor(number, *fields):
    return {'constructor': number, 'fields': list(fields)}


def pubkey(value):
    return constructor(0, constructor(0, {'bytes': value}), constructor(1))


def job():
    return {'simulated_escrow': False, 'payment': {'inputHash': '1' * 64, 'sellerVKey': SELLER,
        'blockchainIdentifier': '2b0360740ec01c60cc02c60230098800', 'smartContractAddress': CONTRACT,
        'rawTimes': dict(zip(('payByTime', 'submitResultTime', 'unlockTime', 'externalDisputeUnlockTime'),
                            ('2000000000000', '2000001200000', '2000002200000', '2000003200000'))),
        'RequestedFunds': [{'unit': '', 'amount': '10000000'}]},
        'result_hash': '2' * 64, 'chain_transactions': [{'tx_hash': TX}]}


def datum(state=0):
    return constructor(0, pubkey(BUYER), pubkey(SELLER), {'bytes': '12'}, {'bytes': '34'},
        {'bytes': '56'}, {'bytes': '78'}, {'int': 2000000}, {'bytes': '1' * 64},
        {'bytes': '2' * 64 if state == 1 else ''}, {'int': 2000000000000},
        {'int': 2000001200000}, {'int': 2000002200000}, {'int': 2000003200000},
        {'int': 0}, {'int': 0}, constructor(state))


def utxo(address=CONTRACT, quantity='12000000', **extra):
    return {'address': address, 'amount': [{'unit': 'lovelace', 'quantity': quantity}],
            'data_hash': DATUM if address == CONTRACT else None, 'output_index': 0,
            'collateral': False, 'reference': False, **extra}


GENESIS = {'network_magic': 1}


def responses(state=0):
    return {'genesis': dict(GENESIS), f'txs/{TX}': {'hash': TX, 'block': BLOCK, 'block_height': 123, 'valid_contract': True},
        f'txs/{TX}/utxos': {'hash': TX, 'inputs': [], 'outputs': [utxo()]},
        f'scripts/datum/{DATUM}': {'json_value': datum(state)}}


async def verify(data, current_job=None, **kwargs):
    calls = []
    def handle(request):
        assert str(request.url).startswith(PREPROD_URL)
        assert request.method == 'GET'
        assert request.headers['project_id'] == 'preprod-test'
        route = str(request.url)[len(PREPROD_URL):]
        if route == 'genesis' and 'genesis' not in data:
            return httpx.Response(200, json=GENESIS)  # every fixture is Preprod unless a test says otherwise
        if route != 'genesis':
            calls.append(route)
        response = data.get(route)
        if isinstance(response, Exception):
            raise response
        if isinstance(response, int):
            return httpx.Response(response, json={'message': 'DO NOT LEAK RESPONSE'})
        return httpx.Response(200, json=response) if response is not None else httpx.Response(404)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        result = await BlockfrostEvidence('preprod-test', client=client).verify(
            current_job or job(), buyer_vkey=BUYER, buyer_address=BUYER_ADDRESS,
            seller_address=SELLER_ADDRESS, **kwargs)
    return result, calls


async def test_matching_funding_is_scoped_not_settlement():
    result, calls = await verify(responses())
    assert result['funding_verified'] is True
    assert result['transactions'][0]['verified_on_chain'] is True
    assert result['transactions'][0]['contract_bound'] is True
    assert result['settlement_verified'] is False
    assert result['blockchain_identifier_verified'] is True
    assert result['limitations']
    assert len(calls) == 3
    assert 'preprod-test' not in str(result)
    assert 'json_value' not in str(result)


async def test_result_output_requires_exact_expected_hash():
    data = responses(1)
    result, _ = await verify(data)
    assert result['result_verified']
    data[f'scripts/datum/{DATUM}']['json_value']['fields'][8]['bytes'] = '3' * 64
    result, _ = await verify(data)
    assert not result['result_verified']


@pytest.mark.parametrize('index,replacement', [
    (0, pubkey('0' * 56)), (1, pubkey('0' * 56)), (7, {'bytes': '0' * 64}),
    (9, {'int': 2000000000001}), (10, {'int': 2000001200001}),
    (11, {'int': 2000002200001}), (12, {'int': 2000003200001}),
    (6, {'int': -1}), (6, {'int': True}), (15, constructor(7)),
    (0, constructor(0, constructor(1, {'bytes': BUYER}), constructor(1))),
])
async def test_wrong_or_malformed_datum_cannot_verify(index, replacement):
    data = responses()
    data[f'scripts/datum/{DATUM}']['json_value']['fields'][index] = replacement
    result, _ = await verify(data)
    assert not result['funding_verified']
    assert not result['result_verified']


@pytest.mark.parametrize('quantity', ['10000000', '-1', '1.2', 'NaN'])
async def test_actual_funds_exclude_datum_collateral(quantity):
    data = responses()
    data[f'txs/{TX}/utxos']['outputs'][0]['amount'][0]['quantity'] = quantity
    result, _ = await verify(data)
    assert not result['funding_verified']


async def test_no_contract_match_does_not_count_as_funding():
    data = responses()
    data[f'txs/{TX}/utxos']['outputs'][0]['address'] = 'addr_test1other'
    result, _ = await verify(data)
    assert result['transactions'][0]['verified_on_chain']
    assert not result['funding_verified']


@pytest.mark.parametrize('marker', ['reference', 'collateral'])
async def test_reference_and_collateral_utxos_are_not_payment_events(marker):
    data = responses()
    data[f'txs/{TX}/utxos']['outputs'][0][marker] = True
    result, _ = await verify(data)
    assert not result['funding_verified']


async def test_hash_inclusion_does_not_imply_contract_passed():
    data = responses()
    data[f'txs/{TX}']['valid_contract'] = False
    result, calls = await verify(data)
    assert result['transactions'][0]['status'] == 'invalid_contract'
    assert not result['funding_verified']
    assert len(calls) == 1


async def test_404_is_pending_and_can_be_retried_read_only():
    result, _ = await verify({})
    assert result['transactions'][0]['status'] == 'pending'
    assert not result['transactions'][0]['verified_on_chain']
    result, _ = await verify(responses())
    assert result['funding_verified']


@pytest.mark.parametrize('code', [302, 403, 429, 500])
async def test_provider_errors_are_sanitized_and_not_verified(code):
    result, _ = await verify({f'txs/{TX}': code})
    item = result['transactions'][0]
    assert item['status'] == 'provider_error'
    assert item['http_status'] == code
    assert not item['verified_on_chain']
    assert 'DO NOT LEAK' not in str(result)


async def test_transaction_and_utxo_response_identities_are_checked():
    for route in (f'txs/{TX}', f'txs/{TX}/utxos'):
        data = responses()
        data[route]['hash'] = TX2
        result, _ = await verify(data)
        assert result['transactions'][0]['status'] == 'invalid_evidence'
        assert not result['funding_verified']


async def test_multiple_transactions_deduplicated_without_stopping_on_pending():
    data = responses()
    data[f'txs/{TX2}'] = {**data[f'txs/{TX}'], 'hash': TX2}
    data[f'txs/{TX2}/utxos'] = {'hash': TX2, 'inputs': [], 'outputs': []}
    result, calls = await verify(data, tx_hashes=[TX, TX2, '3' * 64])
    assert len(result['transactions']) == 3
    assert calls.count(f'txs/{TX}') == 1
    assert result['funding_verified']
    assert result['transactions'][-1]['status'] == 'pending'


@pytest.mark.parametrize('state,recipient,key', [
    (1, SELLER_ADDRESS, 'payout_observed'), (2, BUYER_ADDRESS, 'refund_observed')])
async def test_consumed_contract_and_net_gain_are_observations_only(state, recipient, key):
    data = responses(state)
    data[f'txs/{TX}/utxos'] = {'hash': TX, 'inputs': [utxo(), utxo(recipient, '3000000')],
                              'outputs': [utxo(recipient, '14000000')]}
    result, _ = await verify(data)
    assert result[key]
    assert result['transactions'][0][key.replace('_observed', '_net_lovelace')] == '11000000'
    assert result['settlement_verified'] is False
    data[f'txs/{TX}/utxos']['outputs'][0]['amount'][0]['quantity'] = '2000000'
    result, _ = await verify(data)
    assert not result[key]  # Change output is not proceeds.


async def test_continuing_contract_or_multiple_matches_cannot_be_payout():
    for inputs, outputs in (([utxo()], [utxo(), utxo(SELLER_ADDRESS)]),
                            ([utxo(), utxo(output_index=1)], [utxo(SELLER_ADDRESS)])):
        data = responses(1)
        data[f'txs/{TX}/utxos'] = {'hash': TX, 'inputs': inputs, 'outputs': outputs}
        result, _ = await verify(data)
        assert not result['payout_observed']


@pytest.mark.parametrize('key', ['mainnet-secret', 'preview-secret', '', None])
def test_only_preprod_project_credentials(key):
    with pytest.raises(ValueError, match='Preprod'):
        BlockfrostEvidence(key)


async def test_invalid_identity_and_hash_rejected_before_network():
    for mutation in ('key', 'input', 'contract', 'tx', 'simulated'):
        current = copy.deepcopy(job())
        if mutation == 'key':
            current['payment']['sellerVKey'] = 'bad'
        elif mutation == 'input':
            current['payment']['inputHash'] = 'bad'
        elif mutation == 'contract':
            current['payment']['smartContractAddress'] = 'addr1mainnet'
        elif mutation == 'simulated':
            current['simulated_escrow'] = True
        else:
            current['chain_transactions'][0]['tx_hash'] = '../secret'
        with pytest.raises(ValueError):
            await verify({}, current)


@pytest.mark.parametrize('parts,expected', [
    (('12', '34', '56', '78'), '2b0360740ec01c60cc02c60230098800'),
    (('dd'*32, 'cc'*64, 'aa'*32, 'bb'*26),
     '218d7c6574f41d008c9c96ade8e7d70319ff06147126967916557525c009838d3ccbadbec7ed0000'),
    (('abcd', '1234', '0123456789abcdef'*10, 'fedcba9876543210'*10),
     '0304604c0cc02c0ac06c0760070138086023031804c0a6019a892c8aa9ae871d3ccbadbe478359f654c9b790d5ce974546d5fb776c2b9b217c260de9d5ac8200e809e1c5831a144811c185021810c157acddb77ec3c74c6ad3af41a326d5d8b8facbb3f72d39bafcc1cad9d6d037d3d427c3c4202a383fdbddde38094f8950480000'),
])
def test_identifiers_match_pinned_official_js_150_vectors(parts, expected):
    from cardano_card.chain_evidence import _identifier
    assert _identifier(*parts) == expected


@pytest.mark.parametrize('index', [2, 3, 4, 5])
async def test_all_identifier_components_must_match(index):
    data = responses()
    data[f'scripts/datum/{DATUM}']['json_value']['fields'][index]['bytes'] = '99'
    result, _ = await verify(data)
    assert not result['blockchain_identifier_verified']
    assert not result['funding_verified']


async def test_missing_simulation_mode_rejected():
    current = job()
    del current['simulated_escrow']
    with pytest.raises(ValueError):
        await verify({}, current)

# Real Preprod addresses are public references, never signing credentials.
REAL_BUYER = 'addr_test1qqkmeg88yurmsc4awy7jl7y8l8wg6su65sa7p5vpvcxu7scdwyaxqlg8ep604yk3ap4a3fauyzk7na5746r2wxcljt5s006suf'
REAL_SELLER = 'addr_test1qp30fkp0fs40zhmukxm633nkfy5lf02s9a5yjdwx64ypw0p94zyl0q93enhe2nu4h76e0qa4sc3n2r24jl33pu2zlvvqevvhcw'
REAL_CONTRACT = 'addr_test1wz7j4kmg2cs7yf92uat3ed4a3u97kr7axxr4avaz0lhwdsqukgwfm'
REAL_FEE = 'addr_test1qqfuahzn3rpnlah2ctcdjxdfl4230ygdar00qxc32guetexyg7nun6hggw9g2gpnayzf22sksr0aqdgkdcvqpc2stwtqt4u496'
REAL_BUYER_KEY = '2dbca0e72707b862bd713d2ff887f9dc8d439aa43be0d181660dcf43'
REAL_SELLER_KEY = '62f4d82f4c2af15f7cb1b7a8c6764929f4bd502f684935c6d548173c'
REAL_SCRIPT_HASH = 'bd2adb685621e224aae7571cb6bd8f0beb0fdd31875eb3a27feee6c0'
REDEEMER = '8' * 64


def strict_fixture(kind='payout'):
    current = job()
    current['payment']['sellerVKey'] = REAL_SELLER_KEY
    current['payment']['smartContractAddress'] = REAL_CONTRACT
    data = responses(1 if kind == 'payout' else 2)
    fields = data[f'scripts/datum/{DATUM}']['json_value']['fields']
    for index, key, stake in ((0, REAL_BUYER_KEY, '0d713a607d07c874fa92d1e86bd8a7bc20ade9f69eae86a71b1f92e9'),
                              (1, REAL_SELLER_KEY, '25a889f780b1ccef954f95bfb59783b58623350d5597e310f142fb18')):
        fields[index] = constructor(0, constructor(0, {'bytes': key}),
                                    constructor(0, constructor(0, constructor(0, {'bytes': stake}))))
    data[f'txs/{TX}'].update(fees='200000', asset_mint_or_burn_count=0, withdrawal_count=0, deposit='0')
    escrow = utxo(REAL_CONTRACT, data_hash=DATUM, tx_hash='0'*64, output_index=0)
    recipient = REAL_SELLER if kind == 'payout' else REAL_BUYER
    ordinary = utxo(recipient, '3000000', tx_hash='9'*64, output_index=1)
    if kind == 'payout':
        outputs = [utxo(REAL_SELLER, '11364770'), utxo(REAL_BUYER, '2000000'), utxo(REAL_FEE, '1435230')]
    else:
        outputs = [utxo(REAL_BUYER, '14800000')]
    data[f'txs/{TX}/utxos'] = {'hash': TX, 'inputs': [ordinary, escrow], 'outputs': outputs}
    data[f'txs/{TX}/redeemers'] = [{'purpose': 'spend', 'tx_index': 0,
        'script_hash': REAL_SCRIPT_HASH, 'redeemer_data_hash': REDEEMER}]
    data[f'scripts/datum/{REDEEMER}'] = {'json_value': constructor(0 if kind == 'payout' else 3)}
    policy = {'fee_permille': 50, 'fee_address': REAL_FEE, 'smart_contract_address': REAL_CONTRACT}
    return current, data, policy


async def strict_verify(current, data, policy, **kwargs):
    def handle(request):
        route = str(request.url)[len(PREPROD_URL):]
        value = data.get(route)
        return httpx.Response(200, json=value) if value is not None else httpx.Response(404)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        return await BlockfrostEvidence('preprod-test', client=client).verify(current,
            buyer_vkey=REAL_BUYER_KEY, buyer_address=REAL_BUYER, seller_address=REAL_SELLER,
            settlement_policy=policy, **kwargs)


@pytest.mark.parametrize('kind', ['payout', 'refund'])
async def test_strict_redeemer_bound_unbatched_ada_settlement(kind):
    current, data, policy = strict_fixture(kind)
    result = await strict_verify(current, data, policy)
    assert result['blockchain_identifier_verified']
    assert result['settlement_verified']
    assert result['settlement_kind'] == kind
    assert result['proof_scope'] == 'indexed_unbatched_ada_settlement'
    assert result['transactions'][0]['settlement_kind'] == kind


@pytest.mark.parametrize('mutation', ['hash', 'index', 'action', 'missing', 'multiple', 'purpose'])
async def test_settlement_requires_exact_redeemer_for_consumed_input(mutation):
    current, data, policy = strict_fixture()
    redeemer = data[f'txs/{TX}/redeemers'][0]
    if mutation == 'hash':
        redeemer['script_hash'] = '0'*56
    elif mutation == 'index':
        redeemer['tx_index'] = 1
    elif mutation == 'action':
        data[f'scripts/datum/{REDEEMER}'] = {'json_value': constructor(3)}
    elif mutation == 'missing':
        del data[f'txs/{TX}/redeemers']
    elif mutation == 'purpose':
        redeemer['purpose'] = 'mint'
    else:
        data[f'txs/{TX}/redeemers'].append(dict(redeemer))
    result = await strict_verify(current, data, policy)
    assert not result['settlement_verified']


@pytest.mark.parametrize('mutation', ['fee_address', 'fee_permille', 'contract', 'none'])
async def test_payout_policy_must_match_exact_source_and_fee(mutation):
    current, data, policy = strict_fixture()
    if mutation == 'none':
        policy = None
    elif mutation == 'contract':
        policy['smart_contract_address'] = 'addr_test1other'
    elif mutation == 'fee_address':
        policy['fee_address'] = REAL_BUYER
    else:
        policy['fee_permille'] = 500
    result = await strict_verify(current, data, policy)
    assert not result['settlement_verified']


@pytest.mark.parametrize('mutation', ['mixed_inputs', 'diverted_outputs', 'wrong_fee', 'wrong_collateral',
                                     'assets', 'mint', 'deposit', 'withdrawal', 'result', 'stake', 'conservation'])
async def test_ambiguous_or_invalid_economic_flow_cannot_pass(mutation):
    current, data, policy = strict_fixture()
    utxos = data[f'txs/{TX}/utxos']
    if mutation == 'mixed_inputs':
        utxos['inputs'][0]['address'] = REAL_FEE
    elif mutation == 'diverted_outputs':
        utxos['outputs'][0]['address'] = REAL_FEE
    elif mutation == 'wrong_fee':
        utxos['outputs'][2]['amount'][0]['quantity'] = '1435231'
        utxos['outputs'][0]['amount'][0]['quantity'] = '11364769'
    elif mutation == 'wrong_collateral':
        utxos['outputs'][1]['amount'][0]['quantity'] = '2000001'
        utxos['outputs'][0]['amount'][0]['quantity'] = '11364769'
    elif mutation == 'assets':
        utxos['outputs'][0]['amount'].append({'unit': 'abcd', 'quantity': '1'})
    elif mutation in ('mint', 'deposit', 'withdrawal'):
        key, value = {'mint': ('asset_mint_or_burn_count', 1), 'deposit': ('deposit', '1'),
                      'withdrawal': ('withdrawal_count', 1)}[mutation]
        data[f'txs/{TX}'][key] = value
    elif mutation == 'result':
        current['result_hash'] = '0'*64
    elif mutation == 'stake':
        fields = data[f'scripts/datum/{DATUM}']['json_value']['fields']
        fields[1]['fields'][1]['fields'][0]['fields'][0]['fields'][0]['bytes'] = '0'*56
    else:
        utxos['outputs'][0]['amount'][0]['quantity'] = '11364771'
    result = await strict_verify(current, data, policy)
    assert not result['settlement_verified']


@pytest.mark.parametrize('kind', ['payout', 'refund'])
async def test_native_tokens_in_wallet_change_are_allowed_when_conserved(kind):
    # Real Preprod refund d7a7d824…: the buyer wallet input and change both carry a test token.
    current, data, policy = strict_fixture(kind)
    utxos = data[f'txs/{TX}/utxos']
    wallet = REAL_SELLER if kind == 'payout' else REAL_BUYER
    utxos['inputs'][0]['amount'].append({'unit': 'ab' * 28 + '01', 'quantity': '100'})
    change = next(e for e in utxos['outputs'] if e['address'] == wallet)
    change['amount'].append({'unit': 'ab' * 28 + '01', 'quantity': '100'})
    result = await strict_verify(current, data, policy)
    assert result['settlement_verified'] and result['settlement_kind'] == kind
    change['amount'][-1]['quantity'] = '99'  # a token leaving to nowhere breaks conservation
    assert not (await strict_verify(current, data, policy))['settlement_verified']


@pytest.mark.parametrize('target', ['fee', 'collateral'])
async def test_recipient_outputs_stay_ada_only(target):
    current, data, policy = strict_fixture('payout')
    utxos = data[f'txs/{TX}/utxos']
    utxos['inputs'][0]['amount'].append({'unit': 'ab' * 28 + '01', 'quantity': '1'})
    utxos['outputs'][2 if target == 'fee' else 1]['amount'].append({'unit': 'ab' * 28 + '01', 'quantity': '1'})
    assert not (await strict_verify(current, data, policy))['settlement_verified']


async def test_refund_can_be_verified_without_payout_policy():
    current, data, _ = strict_fixture('refund')
    result = await strict_verify(current, data, None)
    assert result['settlement_verified'] and result['settlement_kind'] == 'refund'


async def test_refund_directly_from_locked_state_with_correct_redeemer():
    current, data, policy = strict_fixture('refund')
    data[f'scripts/datum/{DATUM}']['json_value']['fields'][15] = constructor(0)
    result = await strict_verify(current, data, policy)
    assert result['settlement_verified'] and result['settlement_kind'] == 'refund'


def test_address_checksum_and_network_are_checked():
    from cardano_card.chain_evidence import _address_bytes
    assert _address_bytes(REAL_CONTRACT).hex() == '70' + REAL_SCRIPT_HASH
    assert _address_bytes(REAL_BUYER)[1:29].hex() == REAL_BUYER_KEY
    with pytest.raises(ValueError):
        _address_bytes(REAL_BUYER[:-1] + 'q')
    with pytest.raises(ValueError):
        _address_bytes(REAL_BUYER.replace('addr_test1', 'addr1'))


async def test_explicit_seller_key_cannot_override_payment_identity():
    with pytest.raises(ValueError):
        await verify({}, seller_vkey='0'*56)


async def test_input_reference_is_not_a_spend_redeemer_index():
    current, data, policy = strict_fixture()
    # Reference input sorts before the spent escrow but must not shift pointer 0.
    data[f'txs/{TX}/utxos']['inputs'].append(utxo(REAL_CONTRACT, tx_hash='0'*64,
                                               output_index=-1, reference=True))
    result = await strict_verify(current, data, policy)
    assert result['settlement_verified']


async def test_unrelated_input_reference_does_not_prove_escrow_consumption():
    current, data, policy = strict_fixture()
    data[f'txs/{TX}/utxos']['inputs'][1]['reference'] = True
    result = await strict_verify(current, data, policy)
    assert not result['settlement_verified']


COLLECTION = 'addr_test1qrgpga399l0r8fg7n0jfshhxsjl26w0uslxf5m02yclur8mremst4rk8xsz9lx78e9sdtjfsyj3c9kll2c4958uhkals2qrm9q'


def custom_collection_fixture():
    current, data, policy = strict_fixture()
    current['payment']['payoutAddress'] = COLLECTION
    # Pinned node outputs escrow less protocol fee in full to collection;
    # the seller pays the separate collateral return plus transaction fee.
    data[f'txs/{TX}/utxos']['outputs'] = [
        utxo(COLLECTION, '10564770'), utxo(REAL_SELLER, '800000'),
        utxo(REAL_BUYER, '2000000'), utxo(REAL_FEE, '1435230')]
    return current, data, policy


async def test_saved_custom_collection_and_signing_wallet_change_are_verified():
    current, data, policy = custom_collection_fixture()
    result = await strict_verify(current, data, policy)
    assert result['settlement_verified'] and result['settlement_kind'] == 'payout'
    assert result['payout_address'] == COLLECTION
    assert result['payout_address_snapshotted'] is True
    assert result['transactions'][0]['payout_net_lovelace'] == '10564770'
    assert current['payment']['sellerVKey'] == REAL_SELLER_KEY


async def test_explicit_collection_must_match_snapshot_before_reading_provider():
    current, data, policy = custom_collection_fixture()
    with pytest.raises(ValueError, match='saved payment terms'):
        await strict_verify(current, data, policy, payout_address=REAL_SELLER)
    result = await strict_verify(current, data, policy, payout_address=COLLECTION)
    assert result['settlement_verified']


async def test_custom_collection_can_be_explicit_for_legacy_unsnapshotted_job():
    current, data, policy = custom_collection_fixture()
    del current['payment']['payoutAddress']
    result = await strict_verify(current, data, policy, payout_address=COLLECTION)
    assert result['settlement_verified']
    assert result['payout_address_snapshotted'] is False


async def test_default_recipient_remains_seller_wallet():
    current, data, policy = strict_fixture()
    result = await strict_verify(current, data, policy)
    assert result['settlement_verified']
    assert result['payout_address'] == REAL_SELLER


@pytest.mark.parametrize('mutation', ['short_collection', 'over_collection', 'wrong_destination',
                                     'collection_as_input', 'wrong_seller_change', 'signer_as_collection'])
async def test_custom_collection_allocation_cannot_be_spoofed(mutation):
    current, data, policy = custom_collection_fixture()
    io = data[f'txs/{TX}/utxos']
    if mutation == 'short_collection':
        io['outputs'][0]['amount'][0]['quantity'] = '10564769'
        io['outputs'][1]['amount'][0]['quantity'] = '800001'
    elif mutation == 'over_collection':
        io['outputs'][0]['amount'][0]['quantity'] = '10564771'
        io['outputs'][1]['amount'][0]['quantity'] = '799999'
    elif mutation == 'wrong_destination':
        io['outputs'][0]['address'] = REAL_SELLER
    elif mutation == 'collection_as_input':
        io['inputs'][0]['address'] = COLLECTION
    elif mutation == 'wrong_seller_change':
        io['outputs'][1]['address'] = COLLECTION
    else:
        fields = data[f'scripts/datum/{DATUM}']['json_value']['fields']
        from cardano_card.chain_evidence import _address_bytes
        fields[1]['fields'][0]['fields'][0]['bytes'] = _address_bytes(COLLECTION)[1:29].hex()
    result = await strict_verify(current, data, policy)
    assert not result['settlement_verified']


@pytest.mark.parametrize('address', [REAL_BUYER, REAL_FEE])
async def test_aliased_collection_roles_cannot_verify(address):
    current, data, policy = custom_collection_fixture()
    current['payment']['payoutAddress'] = address
    data[f'txs/{TX}/utxos']['outputs'][0]['address'] = address
    result = await strict_verify(current, data, policy)
    assert not result['settlement_verified']


@pytest.mark.parametrize('address', ['addr1mainnet', COLLECTION[:-1]+'p', '', 12])
async def test_invalid_collection_snapshots_rejected(address):
    current, data, policy = custom_collection_fixture()
    current['payment']['payoutAddress'] = address
    with pytest.raises(ValueError):
        await strict_verify(current, data, policy)


async def test_refund_remains_bound_to_buyer_with_custom_payout_snapshot():
    current, data, policy = strict_fixture('refund')
    current['payment']['payoutAddress'] = COLLECTION
    result = await strict_verify(current, data, policy)
    assert result['settlement_verified'] and result['settlement_kind'] == 'refund'
    assert result['payout_address'] == COLLECTION


async def test_verification_requires_preprod_genesis():
    data = responses()
    data['genesis'] = {'network_magic': 2}  # Preview
    with pytest.raises(ValueError, match='Preprod'):
        await verify(data)
    data['genesis'] = 404
    with pytest.raises(ValueError, match='Preprod'):
        await verify(data)


async def test_nownodes_provider_uses_root_path_and_api_key_header():
    data, calls = responses(), []
    def handle(request):
        assert str(request.url).startswith(NOWNODES_URL) and '/api/v0/' not in str(request.url)
        assert request.headers['api-key'] == 'nn-key' and 'project_id' not in request.headers
        route = str(request.url)[len(NOWNODES_URL):]
        calls.append(route)
        value = data.get(route)
        return httpx.Response(200, json=value) if value is not None else httpx.Response(404)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        result = await BlockfrostEvidence('nn-key', client=client, provider='nownodes').verify(
            job(), buyer_vkey=BUYER, buyer_address=BUYER_ADDRESS)
    assert calls[0] == 'genesis' and result['provider'] == 'nownodes'
    assert result['funding_verified'] and result['transactions'][0]['reported_by'] == 'nownodes'


def test_provider_selection_and_credentials():
    assert chain_provider({'BLOCKFROST_API_KEY_PREPROD': 'preprodX'}) == ('blockfrost', 'preprodX')
    assert chain_provider({'BLOCKFROST_API_KEY_PREPROD': 'preprodX', 'NOWNODES_API_KEY': 'k'}) == ('nownodes', 'k')
    assert chain_provider({'CHAIN_PROVIDER': 'blockfrost', 'BLOCKFROST_API_KEY_PREPROD': 'preprodX', 'NOWNODES_API_KEY': 'k'}) == ('blockfrost', 'preprodX')
    assert chain_provider({}) == ('blockfrost', '')
    with pytest.raises(ValueError):
        chain_provider({'CHAIN_PROVIDER': 'koios'})
    with pytest.raises(ValueError, match='Preprod'):
        chain_provider({'NOWNODES_API_KEY': 'k', 'NOWNODES_URL': 'https://ada.nownodes.io'})  # mainnet host
    assert chain_credential_ok('nownodes', 'k') and not chain_credential_ok('nownodes', '')
    assert chain_credential_ok('blockfrost', 'preprodX') and not chain_credential_ok('blockfrost', 'mainnetX')
    with pytest.raises(ValueError, match='NOWNodes'):
        BlockfrostEvidence('', provider='nownodes')
    with pytest.raises(ValueError):
        BlockfrostEvidence('k', provider='koios')
