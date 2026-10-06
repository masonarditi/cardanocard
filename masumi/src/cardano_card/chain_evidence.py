"""Read-only, independently indexed Preprod evidence with explicit proof boundaries.

Datum layout: masumi-payment-service 0.22.0 (26c7297821fdc9f803d5cca91bc8d36daf178a1a),
utils/converter/string-datum-convert and utils/generator/contract-generator.
HTTP schemas: https://github.com/blockfrost/openapi/blob/master/openapi.yaml.
This does NOT equate transaction inclusion or recipient outputs with settlement.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

import httpx

PREPROD_URL = 'https://cardano-preprod.blockfrost.io/api/v0/'
HEX64 = re.compile(r'[0-9a-f]{64}')
HEX56 = re.compile(r'[0-9a-f]{56}')
TIMES = ('payByTime', 'submitResultTime', 'unlockTime', 'externalDisputeUnlockTime')
STATES = ('FundsLocked', 'ResultSubmitted', 'RefundRequested', 'Disputed')
LIMITATIONS = [
    'Blockfrost indexed inclusion is not a finality guarantee.',
    'Datum checks bind the exact compressed blockchain identifier, contract, input hash, wallet payment keys, deadlines and amounts; the reference signature itself is not cryptographically reverified.',
    'Full settlement proof is limited to unbatched ADA transactions with exact full addresses, a matched spend redeemer, conserved value and the pinned fee policy; other recipient net gains remain observations.',
    'No result proves merchant purchase or reimbursement of merchant spending.',
]


def _identifier(reference_key, signature, seller_nonce, buyer_nonce):
    """Exact bounded ASCII subset of lz-string 1.5.0 compressToUint8Array.

    Algorithm/bit packing checked against pieroxy/lz-string 1.5.0
    libs/lz-string.js (Copyright Pieroxy 2013, WTFPL v2) and official JS
    generated vectors. Recompressing known datum fields avoids parsing attacker
    controlled compressed data. Only hex fields and separators are accepted.
    """
    parts = (seller_nonce, buyer_nonce, signature, reference_key)
    if any(not isinstance(p, str) or not p or len(p) > 4096 or len(p) % 2 or
           not re.fullmatch('[0-9a-f]+', p) for p in parts):
        raise ValueError('Invalid identifier components')
    text = '.'.join(parts)
    dictionary, literals, bits = {}, set(), []
    next_id, width, remaining = 3, 2, 2

    def emit(number, count):
        bits.extend((number >> bit) & 1 for bit in range(count))

    def grow():
        nonlocal remaining, width
        remaining -= 1
        if remaining == 0:
            remaining = 1 << width
            width += 1

    def word(value):
        if value in literals:
            emit(0, width)  # ASCII only; no UTF-16 literal branch required.
            emit(ord(value), 8)
            literals.remove(value)
            grow()
        else:
            emit(dictionary[value], width)
        grow()

    previous = ''
    for char in text:
        if char not in dictionary:
            dictionary[char] = next_id
            next_id += 1
            literals.add(char)
        candidate = previous + char
        if candidate in dictionary:
            previous = candidate
        else:
            word(previous)
            dictionary[candidate] = next_id
            next_id += 1
            previous = char
    if previous:
        word(previous)
    emit(2, width)
    bits.extend([0] * (16 - len(bits) % 16))
    output = bytearray()
    for offset in range(0, len(bits), 8):
        value = 0
        for bit in bits[offset:offset + 8]:
            value = (value << 1) | bit
        output.append(value)
    return output.hex()


def _hex(value, length):
    return isinstance(value, str) and re.fullmatch(r'[0-9a-f]{%d}' % length, value) is not None


def _integer(value):
    # Plutus JSON integers are numeric, not strings; reject bools and truncation.
    if type(value) is not int:
        raise ValueError('Expected integer')
    return value


def _bytes(value):
    if not isinstance(value, dict) or set(value) != {'bytes'}:
        raise ValueError('Expected byte string')
    result = value['bytes']
    if not isinstance(result, str) or len(result) % 2 or not re.fullmatch('[0-9a-f]*', result):
        raise ValueError('Invalid byte string')
    return result


def _constructor(value, number, count):
    if (not isinstance(value, dict) or type(value.get('constructor')) is not int or
            value['constructor'] != number or not isinstance(value.get('fields'), list) or
            len(value['fields']) != count):
        raise ValueError('Unexpected Plutus constructor')
    return value['fields']


def _payment_key(address):
    # V1 stores Plutus Address {payment_credential, stake_credential}. Only payment
    # key ownership is asserted here, never full address equality.
    fields = _constructor(address, 0, 2)
    key = _bytes(_constructor(fields[0], 0, 1)[0])
    if not HEX56.fullmatch(key):
        raise ValueError('Invalid payment key')
    return key


def _amounts(values):
    if not isinstance(values, list) or not values:
        raise ValueError('Missing amounts')
    result = {}
    for item in values:
        unit, quantity = item['unit'], item['quantity']
        if (not isinstance(unit, str) or not isinstance(quantity, str) or
                not re.fullmatch('[0-9]+', quantity) or unit in result):
            raise ValueError('Invalid amounts')
        result[unit] = int(quantity)
    return result


def _datum_matches(datum, payment, buyer_vkey, seller_vkey):
    fields = _constructor(datum, 0, 16)
    if _payment_key(fields[0]) != buyer_vkey or _payment_key(fields[1]) != seller_vkey:
        return None
    # Validate all fixed-layout fields, even those outside our proof boundary.
    for index in (2, 3, 4, 5, 7, 8):
        _bytes(fields[index])
    identifier = _identifier(*(_bytes(fields[index]) for index in (2, 3, 4, 5)))
    if identifier != payment.get('blockchainIdentifier'):
        return None
    if _bytes(fields[7]) != payment['inputHash']:
        return None
    integers = {}
    for index in (6, 9, 10, 11, 12, 13, 14):
        integers[index] = _integer(fields[index]['int'])
        if integers[index] < 0:
            return None
    for index, key in enumerate(TIMES, 9):
        raw = payment.get('rawTimes', payment)[key]
        if isinstance(raw, bool) or not re.fullmatch('[0-9]+', str(raw)):
            return None
        stamp = int(raw)
        expected = stamp if stamp > 100_000_000_000 else stamp * 1000
        if integers[index] != expected:
            return None
    state = fields[15]
    if type(state.get('constructor')) is not int or state['constructor'] not in range(4):
        return None
    _constructor(state, state['constructor'], 0)
    return {'state': STATES[state['constructor']], 'result_hash': _bytes(fields[8]),
            'collateral_return_lovelace': integers[6], 'address_fields': fields[:2]}


def _funds_match(values, requested, collateral):
    if collateral and collateral < 1435230:
        return False
    actual = _amounts(values)
    seen = set()
    if not isinstance(requested, list) or not requested:
        return False
    for item in requested:
        unit = 'lovelace' if item['unit'] in ('', 'lovelace') else item['unit']
        value = item['amount']
        if (unit in seen or not isinstance(value, str) or not re.fullmatch('[0-9]+', value)
                or int(value) <= 0):
            return False
        seen.add(unit)
        available = actual.get(unit, 0) - (collateral if unit == 'lovelace' else 0)
        if (available < int(value) if unit == 'lovelace' else available != int(value)):
            return False
    return True


class BlockfrostEvidence:
    """Only GETs the fixed Preprod origin; injected clients allow offline tests.

    verify() accepts a persisted engine job, expected buyer payment key, optional
    exact wallet/collection addresses, and additional buyer-side transaction hashes.
    payout_address defaults to payment.payoutAddress, then seller_address; an
    explicit value must match a saved snapshot. The datum seller remains the
    signing wallet, separate from the collection recipient. The
    job's payment fields must already have been checked against the submitted
    request. Returned data deliberately excludes credentials and raw datums.
    """
    def __init__(self, project_id, *, client=None):
        if not isinstance(project_id, str) or not project_id.startswith('preprod'):
            raise ValueError('A Preprod Blockfrost project key is required')
        self.project_id, self.client = project_id, client

    async def _get(self, client, route):
        response = await client.get(PREPROD_URL + route,
                                    headers={'project_id': self.project_id},
                                    follow_redirects=False, timeout=30)
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return response.json()

    async def verify(self, job, *, buyer_vkey, seller_vkey=None, buyer_address=None,
                     seller_address=None, payout_address=None, tx_hashes=(), settlement_policy=None):
        payment = job['payment']
        seller_vkey = seller_vkey or payment.get('sellerVKey')
        if (job.get('simulated_escrow') is not False or not HEX56.fullmatch(buyer_vkey or '') or
                not HEX56.fullmatch(seller_vkey or '') or seller_vkey != payment.get('sellerVKey') or not _hex(payment.get('inputHash'), 64) or
                not str(payment.get('smartContractAddress', '')).startswith('addr_test1')):
            raise ValueError('Real Preprod payment identity and wallet keys are required')
        for address in (buyer_address, seller_address):
            if address is not None and not address.startswith('addr_test1'):
                raise ValueError('Recipient addresses must be testnet addresses')
        snapshot = payment.get('payoutAddress')
        if snapshot is not None:
            _address_bytes(snapshot)
            if payout_address is not None and payout_address != snapshot:
                raise ValueError('Payout recipient differs from the saved payment terms')
        if payout_address is not None:
            _address_bytes(payout_address)
        payout_address = payout_address or snapshot or seller_address
        expected_result = job.get('result_hash')
        if expected_result and not _hex(expected_result, 64):
            raise ValueError('Invalid expected result hash')
        hashes = []
        records = list(job.get('chain_transactions', [])) + list(tx_hashes)
        if len(records) > 200:
            raise ValueError('Too many transaction records')
        for item in records:
            value = item.get('tx_hash') if isinstance(item, dict) else item
            if not isinstance(value, str) or not HEX64.fullmatch(value):
                raise ValueError('Invalid transaction hash')
            if value not in hashes:
                hashes.append(value)
        result = {'network': 'Preprod', 'provider': 'Blockfrost',
                  'checked_at': datetime.now(timezone.utc).isoformat(),
                  'proof_scope': 'indexed_contract_datum_and_value_observation',
                  'payout_address': payout_address, 'payout_address_snapshotted': snapshot is not None,
                  'transactions': [], 'funding_verified': False, 'result_verified': False,
                  'payout_observed': False, 'refund_observed': False,
                  'blockchain_identifier_verified': False, 'settlement_verified': False, 'settlement_kind': None,
                  'limitations': list(LIMITATIONS)}
        if self.client is None:
            async with httpx.AsyncClient() as client:
                await self._verify(client, hashes, payment, buyer_vkey, seller_vkey,
                                   buyer_address, seller_address, payout_address, expected_result, result, settlement_policy)
        else:
            await self._verify(self.client, hashes, payment, buyer_vkey, seller_vkey,
                               buyer_address, seller_address, payout_address, expected_result, result, settlement_policy)
        return result

    async def _verify(self, client, hashes, payment, buyer_vkey, seller_vkey,
                      buyer_address, seller_address, payout_address, expected_result, result, settlement_policy):
        for tx_hash in hashes:
            item = {'tx_hash': tx_hash, 'network': 'Preprod', 'reported_by': 'blockfrost',
                    'verified_on_chain': False, 'contract_matches': [], 'status': 'pending'}
            result['transactions'].append(item)
            try:
                metadata = await self._get(client, 'txs/' + tx_hash)
                if metadata is None:
                    continue
                if (metadata.get('hash') != tx_hash or not _hex(metadata.get('block'), 64) or
                        type(metadata.get('block_height')) is not int):
                    raise ValueError('Transaction response identity mismatch')
                item.update(verified_on_chain=True, block_hash=metadata['block'],
                            block_height=metadata['block_height'], status='included')
                if metadata.get('valid_contract') is not True:
                    item['status'] = 'invalid_contract'
                    continue
                utxos = await self._get(client, f'txs/{tx_hash}/utxos')
                if utxos is None:
                    item['status'] = 'utxos_pending'
                    continue
                if utxos.get('hash') != tx_hash:
                    raise ValueError('UTXO response identity mismatch')
                sides = {side: utxos[side] for side in ('inputs', 'outputs')}
                matched = {'inputs': [], 'outputs': []}
                for side, entries in sides.items():
                    if not isinstance(entries, list):
                        raise ValueError('Invalid UTXOs')
                    for entry in entries:
                        if entry.get('collateral') or entry.get('reference'):
                            continue
                        if entry.get('address') != payment['smartContractAddress']:
                            continue
                        datum_hash = entry.get('data_hash')
                        if not _hex(datum_hash, 64):
                            continue
                        raw = await self._get(client, 'scripts/datum/' + datum_hash)
                        if raw is None:
                            continue
                        datum = _datum_matches(raw['json_value'], payment, buyer_vkey, seller_vkey)
                        if datum is None:
                            continue
                        funds = _funds_match(entry['amount'], payment['RequestedFunds'],
                                             datum['collateral_return_lovelace'])
                        item['contract_matches'].append({'side': side,
                            'output_index': entry.get('output_index'), 'datum_hash': datum_hash,
                            'state': datum['state'], 'requested_funds_verified': funds,
                            'result_hash_matches': bool(expected_result and datum['result_hash'] == expected_result)})
                        matched[side].append((entry, datum, funds))
                item['contract_bound'] = bool(matched['inputs'] or matched['outputs'])
                if item['contract_bound']:
                    result['blockchain_identifier_verified'] = True
                for _, datum, funds in matched['outputs']:
                    if funds and datum['state'] == 'FundsLocked' and not datum['result_hash']:
                        result['funding_verified'] = True
                    if funds and datum['state'] == 'ResultSubmitted' and expected_result and datum['result_hash'] == expected_result:
                        result['result_verified'] = True
                # A terminal observation needs a consumed matching escrow, no successor
                # at this contract, and a positive beneficiary NET gain (not change).
                remaining = any(e.get('address') == payment['smartContractAddress'] for e in sides['outputs'])
                if len(matched['inputs']) == 1 and not remaining:
                    escrow, datum, funds = matched['inputs'][0]
                    if funds:
                        for key, address, expected_state in (
                            ('payout_observed', payout_address, 'ResultSubmitted'),
                            ('refund_observed', buyer_address, 'RefundRequested')):
                            if not address or datum['state'] != expected_state:
                                continue
                            if key == 'payout_observed' and (not expected_result or datum['result_hash'] != expected_result):
                                continue
                            net = self._net_lovelace(sides, address)
                            item[key.replace('_observed', '_net_lovelace')] = str(net)
                            if net > 0:
                                result[key] = True
                        # Strict proof is intentionally narrower than observations.
                        # Unknown/mixed/multi-asset transaction shapes stay incomplete.
                        policy = settlement_policy
                        if policy is not None and policy.get('smart_contract_address') != payment['smartContractAddress']:
                            policy = None
                        kind = _strict_values(metadata, sides, escrow, datum, buyer_address, seller_address, policy, payout_address)
                        if kind == 'payout' and datum['result_hash'] != expected_result:
                            kind = None
                        if kind and await self._redeemer_matches(client, tx_hash, sides['inputs'], escrow,
                                                                  payment['smartContractAddress'], kind):
                            item['settlement_verified'] = True
                            item['settlement_kind'] = kind
                            result['settlement_verified'] = True
                            result['settlement_kind'] = kind
                            result[kind + '_observed'] = True
                            item['proof_checks'] = ['exact_payment_datum', 'full_wallet_addresses',
                                'script_spend_redeemer', 'ada_value_conservation', 'beneficiary_attribution']
                            result['proof_scope'] = 'indexed_unbatched_ada_settlement'
            except httpx.HTTPStatusError as error:
                item['status'] = 'provider_error'
                item['http_status'] = error.response.status_code
            except httpx.HTTPError:
                item['status'] = 'provider_unavailable'
            except (ValueError, KeyError, TypeError, AttributeError):
                item['status'] = 'invalid_evidence'

    async def _redeemer_matches(self, client, tx_hash, inputs, escrow, contract, kind):
        script = _address_bytes(contract)
        if script[0] >> 4 != 7:
            return False
        normal = _normal(inputs)
        for entry in normal:
            if not _hex(entry.get('tx_hash'), 64) or type(entry.get('output_index')) is not int or entry['output_index'] < 0:
                return False
        # Ledger spend pointers index ordinary inputs sorted by (tx_id, index).
        # https://developers.cardano.org/docs/build/smart-contracts/advanced/debug-cbor/
        ordered = sorted(normal, key=lambda e: (e['tx_hash'], e['output_index']))
        if len({(e['tx_hash'], e['output_index']) for e in ordered}) != len(ordered):
            return False
        index = next(i for i, entry in enumerate(ordered) if entry is escrow)
        redeemers = await self._get(client, f'txs/{tx_hash}/redeemers')
        if not isinstance(redeemers, list):
            return False
        spending = [r for r in redeemers if r.get('purpose') == 'spend']
        if len(spending) != 1:
            return False
        redeemer = spending[0]
        if (type(redeemer.get('tx_index')) is not int or redeemer['tx_index'] != index or
                redeemer.get('script_hash') != script[1:29].hex()):
            return False
        datum_hash = redeemer.get('redeemer_data_hash')
        if not _hex(datum_hash, 64):
            return False
        value = await self._get(client, 'scripts/datum/' + datum_hash)
        if value is None:
            return False
        _constructor(value['json_value'], 0 if kind == 'payout' else 3, 0)
        return True

    @staticmethod
    def _net_lovelace(sides, address):
        total = 0
        for side, sign in (('outputs', 1), ('inputs', -1)):
            for entry in sides[side]:
                if entry.get('address') == address and not entry.get('collateral') and not entry.get('reference'):
                    total += sign * _amounts(entry['amount']).get('lovelace', 0)
        return total


def _address_bytes(address):
    """Decode and checksum the CIP-19 addr_test Bech32 encoding (network tag 0)."""
    if not isinstance(address, str) or len(address) > 200 or not address.startswith('addr_test1') or address.lower() != address:
        raise ValueError('Expected lowercase testnet Bech32 address')
    alphabet = 'qpzry9x8gf2tvdw0s3jn54khce6mua7l'
    hrp, encoded = address.rsplit('1', 1)
    values = [alphabet.index(char) for char in encoded]
    if hrp != 'addr_test' or len(values) < 6:
        raise ValueError('Invalid address')
    checksum = 1
    expanded = [ord(char) >> 5 for char in hrp] + [0] + [ord(char) & 31 for char in hrp]
    for value in expanded + values:
        top = checksum >> 25
        checksum = ((checksum & 0x1ffffff) << 5) ^ value
        for index, generator in enumerate((0x3b6a57b2, 0x26508e6d, 0x1ea119fa, 0x3d4233dd, 0x2a1462b3)):
            if (top >> index) & 1:
                checksum ^= generator
    if checksum != 1:
        raise ValueError('Invalid address checksum')
    output, accumulator, bits = bytearray(), 0, 0
    for value in values[:-6]:
        accumulator = (accumulator << 5) | value
        bits += 5
        while bits >= 8:
            bits -= 8
            output.append((accumulator >> bits) & 255)
    if bits >= 5 or (accumulator & ((1 << bits) - 1)) or not output or output[0] & 15:
        raise ValueError('Invalid address encoding or network')
    kind = output[0] >> 4
    if (kind == 0 and len(output) == 57) or (kind in (6, 7) and len(output) == 29):
        return bytes(output)
    raise ValueError('Unsupported address credentials')


def _full_address_matches(datum_address, address):
    decoded = _address_bytes(address)
    fields = _constructor(datum_address, 0, 2)
    if decoded[0] >> 4 not in (0, 6) or _payment_key(datum_address) != decoded[1:29].hex():
        return False
    if len(decoded) == 29:
        _constructor(fields[1], 1, 0)  # No staking credential.
    else:
        stake = _constructor(_constructor(_constructor(fields[1], 0, 1)[0], 0, 1)[0], 0, 1)[0]
        if _bytes(stake) != decoded[29:].hex():
            return False
    return True


def _normal(entries):
    return [entry for entry in entries if entry.get('collateral') is False and entry.get('reference', False) is False]


def _ada(entry):
    amounts = _amounts(entry['amount'])
    if set(amounts) != {'lovelace'}:
        raise ValueError('Strict settlement supports ADA only')
    return amounts['lovelace']


def _strict_values(metadata, sides, escrow, datum, buyer_address, seller_address, policy, payout_address):
    """Return terminal kind only for fully attributable, unbatched ADA value flow."""
    if (metadata.get('asset_mint_or_burn_count') != 0 or metadata.get('withdrawal_count') != 0 or
            metadata.get('deposit') != '0' or metadata.get('treasury_donation', '0') != '0'):
        return None
    fee = metadata.get('fees')
    if not isinstance(fee, str) or not re.fullmatch('[0-9]+', fee):
        return None
    fee = int(fee)
    inputs, outputs = _normal(sides['inputs']), _normal(sides['outputs'])
    if sum(_ada(e) for e in inputs) != sum(_ada(e) for e in outputs) + fee:
        return None
    if buyer_address == seller_address or not buyer_address or not seller_address:
        return None
    if not _full_address_matches(datum['address_fields'][0], buyer_address):
        return None
    if not _full_address_matches(datum['address_fields'][1], seller_address):
        return None
    locked = _ada(escrow)
    if datum['state'] in ('FundsLocked', 'RefundRequested') and not datum['result_hash']:
        if any(e is not escrow and e['address'] != buyer_address for e in inputs):
            return None
        if not outputs or any(e['address'] != buyer_address for e in outputs):
            return None
        if locked - fee <= 0:
            return None
        return 'refund'
    if datum['state'] != 'ResultSubmitted' or not datum['result_hash'] or not isinstance(policy, dict):
        return None
    permille, fee_address = policy.get('fee_permille'), policy.get('fee_address')
    if type(permille) is not int or not 0 <= permille <= 1000:
        return None
    _address_bytes(fee_address)
    payout_address = payout_address or seller_address
    _address_bytes(payout_address)
    if len({buyer_address, seller_address, fee_address}) != 3:
        return None
    if payout_address in (buyer_address, fee_address):
        return None  # Role aliases cannot establish separate recipient allocations.
    if any(e is not escrow and e['address'] != seller_address for e in inputs):
        return None
    if any(e['address'] not in (buyer_address, seller_address, payout_address, fee_address) for e in outputs):
        return None
    protocol_fee = max(1435230, locked * permille // 1000)  # Pinned node 0.22 collection handler.
    if sum(_ada(e) for e in outputs if e['address'] == fee_address) != protocol_fee:
        return None
    collateral = datum['collateral_return_lovelace']
    if sum(_ada(e) for e in outputs if e['address'] == buyer_address) != collateral:
        return None
    if payout_address != seller_address:
        # Pinned collection handler sends locked-fee to the collection address.
        # transaction-generator adds buyer collateral and pays ledger fees from
        # seller wallet inputs; all remaining wallet value returns as change.
        collected = sum(_ada(e) for e in outputs if e['address'] == payout_address)
        seller_inputs = sum(_ada(e) for e in inputs if e['address'] == seller_address)
        seller_change = sum(_ada(e) for e in outputs if e['address'] == seller_address)
        if collected != locked - protocol_fee or collected <= 0:
            return None
        if seller_change - seller_inputs != -collateral - fee:
            return None
    elif locked - protocol_fee - collateral - fee <= 0:
        return None
    return 'payout'
