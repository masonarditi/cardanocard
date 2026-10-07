# Masumi Web3CardanoV2 escrow: on-chain datum / redeemer layout (Preprod)

Research notes for extending `masumi/src/cardano_card/chain_evidence.py` (`_datum_matches`, `_constructor`,
`_strict_values`, `_redeemer_matches`) from the V1 contract to V2. Written 2026-10-07 from the public repo
`masumi-network/masumi-payment-service` (branch `main`) plus live Preprod data read through the public Koios API
(`https://preprod.koios.rest/api/v1`, no key). Anything not confirmed by source or chain data is marked UNVERIFIED.

Sources (paths are in `masumi-network/masumi-payment-service@main`; `L` = line):
- `smart-contracts/payment-v2/validators/vested_pay.ak` — the Aiken validator (hereafter `vested_pay.ak`).
- `smart-contracts/payment-v2/plutus.json` — blueprint: compiler Aiken `v1.1.23+8949565`, `plutusVersion: v3`,
  `validators[0].title = vested_pay.vested_pay.spend`, `hash = 2d6abca32e4b22b59e948ef22dfe682017de917a9ec088aa1bc3c64e`
  (UNPARAMETERISED hash; the on-chain hash `a15ce9d82d2f67645fc624e2edac03c6f1c106d0ad1af5815a3b14ad` is the result
  of `applyParamsToScript`, confirmed by Koios `plutus_contracts[].script_hash` on every spend below).
- `packages/payment-source-v2/src/contract-generator.ts` — `getPaymentScriptV2` L50-77, `getDatumV2` L193-304.
- `packages/payment-source-v2/src/datum-builder.ts` — `createDatumFromBlockchainIdentifierV2` L51-70,
  `createDatumFromDecodedContractV2` L72-94.
- `packages/payment-source-v2/src/builders/redeemer-data.ts` — `generateRedeemerData` L32-61.
- `packages/payment-source-v2/src/builders/withdrawal-outputs.ts` — `addMinimumAdaOutput` L13-27,
  `addWithdrawalOutputs` L29-83, batch doc comment L85-103.
- `packages/payment-source-v2/src/services/payments/collection/service.ts` — payout assembly L199-281, batch L714-724.
- `packages/payment-source-v2/src/services/purchases/collect-refund/service.ts` — refund assembly L171-209, batch L620-630.
- `packages/payment-core/src/smart-contract-state.ts` L3-10; `packages/payment-core/src/config.ts` L466-469.
- `packages/payment-core/src/blockchain-identifier.ts` — `generateBlockchainIdentifier` L13-27, decode L29-70.
- `src/utils/converter/string-datum-convert/index.ts` — `decodeV2ContractDatum` L240-385,
  `serializeOptionalAddressObj` L44-63, `valueToStatus` L453-480, `MAX_DATUM_INT = 2^63-1` L238.
- `smart-contracts/payment-v2/README.md` L14, L111-141 (return-address rules), L144-168 (state/action tables),
  L171-181 (Withdraw), L225-235 (WithdrawRefund); `smart-contracts/payment-v2/state_machine_diagram.md`.
- `docs/migrations/v2-contract-cip30-upgrade.md` L15-29 — Preprod address
  `addr_test1wzs4e6wc95hkwezlccjw9mdvq0r0rsgx6zk34avptga3ftgn37w4g` = default seed admin wallets,
  `DEFAULT_ADMIN_SIGNATURES_V2 = 2`, `COOLDOWN_TIME = 7 min`. Which admin keys: UNVERIFIED (not needed for us).
- V1 for comparison: `smart-contracts/payment/validators/vested_pay.ak` (Datum L22-39, Action L41-56, params
  L59-63 incl. `fee_address`/`fee_permille`, fee check L133-159).

## 1. V2 datum (`Constr 0`, exactly 19 fields)

`vested_pay.ak` L41-61 (`pub type Datum`); built by `getDatumV2` (contract-generator.ts L277-303); decoded by
`decodeV2ContractDatum` (L252 rejects anything but 19 fields). Index → name → Plutus JSON shape:

| # | field | shape (Blockfrost/Koios `json_value`) | notes |
|---|-------|---------------------------------------|-------|
| 0 | `buyer` | `Address` (see below) | payment key MUST be a vkey (validator derives signer from it) |
| 1 | `buyer_return_address` | `Option<Address>`: `{constructor:0, fields:[Address]}` or `{constructor:1, fields:[]}` | NEW |
| 2 | `seller` | `Address` | vkey only |
| 3 | `seller_return_address` | `Option<Address>` | NEW |
| 4 | `reference_key` | `bytes` | live: 42 bytes (CBOR COSE_Key), V1 was a 32-byte key |
| 5 | `reference_signature` | `bytes`, length >= 16 bytes enforced on-chain (L133-134) | live: 217 bytes (CIP-30 COSE_Sign1) |
| 6 | `seller_nonce` | `bytes` | live: 32 bytes |
| 7 | `buyer_nonce` | `bytes` | live: 10 bytes |
| 8 | `agent_identifier` | `bytes` (`''` when absent) | NEW; live: 60 bytes = registry policy `67ab0c92…` (28) + asset name (32) |
| 9 | `collateral_return_lovelace` | `int` >= 0 (enforced L127) | |
| 10 | `input_hash` | `bytes` (`''` = none) | |
| 11 | `result_hash` | `bytes` (`''` = none) | can be CLEARED again by `AuthorizeRefund` |
| 12 | `pay_by_time` | `int` POSIX **ms** | live: `1783078691243` |
| 13 | `submit_result_time` | `int` ms | = `resultTime` in the TS code |
| 14 | `unlock_time` | `int` ms | |
| 15 | `external_dispute_unlock_time` | `int` ms | |
| 16 | `seller_cooldown_time` | `int` ms | NEW position (V1 index 13) |
| 17 | `buyer_cooldown_time` | `int` ms | |
| 18 | `state` | `{constructor: 0..5, fields: []}` | see section 2 |

Address encoding (contract-generator.ts L120-140, `mPubKeyAddress`; identical to V1, so `_payment_key` and
`_full_address_matches` work unchanged on fields 0/2 and on the inner Address of fields 1/3):
`{constructor:0, fields:[{constructor:0, fields:[{bytes: pkh28}]}, STAKE]}` with
`STAKE = {constructor:0, fields:[{constructor:0, fields:[{constructor:0, fields:[{bytes: skh28}]}]}]}` (Some(Inline(VerificationKey)))
or `{constructor:1, fields:[]}` (enterprise, no stake). Spec test: `contract-generator.spec.ts` L39-75.
Return addresses are only compared as full `Address` values by the validator (`output.address == expected`,
L795-800), so on-chain they may be any shape (README L100-109); the Masumi API currently writes base addresses.

Identifier (`decodeV2ContractDatum` L344-355, `blockchain-identifier.ts` L13-27): segments joined with `.` then
lz-string `compressToUint8Array` → hex, order `sellerIdentifier.buyerNonce.referenceSignature.referenceKey[.smartContractAddress]`
where `sellerIdentifier = seller_nonce + agent_identifier` (hex concat, unless seller_nonce is already > 64 hex chars).
The decoder always appends the 5th segment (the script address) when it knows it; `decodeBlockchainIdentifier` L53 accepts
4 or 5 segments. Which form the Masumi node sends to `/start_job` for V2 jobs: UNVERIFIED — our verifier should compute
both the 4- and 5-segment variants and accept either. Our `_identifier()` (chain_evidence.py L55-66) currently
compresses exactly 4 hex parts; the 5th part is a Bech32 string (lowercase letters + digits + `_`), outside its hex-only
alphabet, so the ASCII subset must be widened for the 5-segment variant.

Live sample (Koios `address_utxos`, 2026-10-07): 1000 unspent UTxOs at the V2 address, all 19 fields; states
5:627, 0:248, 3:66, 1:59; return addresses (Some,Some): 872, (None,None): 128. Example UTxO
`b9faffeda9748d0f4e5073b46052080a82376d271a0b888b2d2f4e06e067890a#0` (4,986,670 lovelace): collateral 4,452,190,
`input_hash` 32 bytes, `result_hash` empty, `state` 5, `seller_cooldown_time` set, `buyer_cooldown_time` 0.

## 2. State constructor indices

`vested_pay.ak` L32-39, `smart-contract-state.ts` L3-10, `getSmartContractStateDatum` L156-191, `valueToStatus` L453-480:
`0 FundsLocked`, `1 ResultSubmitted`, `2 RefundRequested`, `3 Disputed`, `4 WithdrawAuthorized`, `5 RefundAuthorized`.
All nullary. Our `STATES` tuple (chain_evidence.py L46) and the `range(4)` check (L195) need indices 4 and 5.

Transitions (validator branches): `SubmitResult` → `ResultSubmitted` from FundsLocked/ResultSubmitted, else `Disputed`
(L643-651); `SetRefundRequested` → `RefundRequested` if no result_hash else `Disputed`, from FundsLocked/ResultSubmitted/
Disputed (L349-404); `AuthorizeWithdrawal` Disputed → `WithdrawAuthorized`, requires result_hash (L460-505);
`AuthorizeRefund` → `RefundAuthorized` with result_hash CLEARED, from FundsLocked/ResultSubmitted/RefundRequested/Disputed
(L702-762). No `UnSetRefundRequested`/cancel-refund in V2 (state_machine_diagram.md "Behavior changes vs V1").

## 3. Redeemer (`Action`) constructor indices

`vested_pay.ak` L75-94; plutus.json `definitions["vested_pay/Action"]`; `generateRedeemerData` L42-53:

| idx | Action | fields | who signs | used by Masumi for |
|-----|--------|--------|-----------|--------------------|
| 0 | `Withdraw` | none | seller | `CollectCompleted` = payout (collection/service.ts L350, L714) |
| 1 | `SetRefundRequested` | none | buyer | request refund |
| 2 | `AuthorizeWithdrawal` | none | buyer | buyer gives up a dispute |
| 3 | `WithdrawRefund` | none | buyer | `CollectRefund` = refund payout (collect-refund/service.ts L272, L620) |
| 4 | `WithdrawDisputed` | `buyer_value: AssetValue`, `seller_value: AssetValue`, `admin_signatures: List<AdminSignature>` | anyone + admin CIP-8 sigs | admin dispute settlement (not built by the node) |
| 5 | `SubmitResult` | none | seller | submit result |
| 6 | `AuthorizeRefund` | none | seller | seller-authorised refund |

`AssetValue = Pairs<ByteArray, Pairs<ByteArray, Int>>` (map); `AdminSignature = Constr 0 [vk bytes, protected_headers bytes,
signature bytes]`. There is NO cancel-refund redeemer (redeemer-data.ts L24-30). Our `_redeemer_matches` already uses
`0` for payout and `3` for refund (chain_evidence.py L444) — the indices are unchanged from V1 (V1 also used 0/3; V1 index 2
was `UnSetRefundRequested`, V2 index 2 is `AuthorizeWithdrawal`). Observed on Preprod via Koios `tx_info` (40 latest
txs at the address): spend redeemers with constructors 0 (7 txs), 3 (4), 5 (many), 1 (1), 6 (1); all `purpose: spend`.

## 4. How V2 payout / refund transactions distribute value

Validator rules (`Withdraw` L254-347, `WithdrawRefund` L407-458, `outputs_with_reference_tag` L773-805):
- Every payout output the validator counts must be **tagged**: inline datum equal to the spent escrow `OutputReference`,
  i.e. `{constructor:0, fields:[{bytes: <escrow tx_hash 32B>}, {int: <escrow output_index>}]}` (mesh `mOutputReference`),
  AND sit at exactly the expected address (`return_address` if `Some`, else the datum principal). Untagged outputs are ignored.
- `Withdraw` (payout): allowed when `state == WithdrawAuthorized` (no time gate) OR `state == ResultSubmitted` and
  `validity_range.lower >= unlock_time`; seller must sign; `result_hash` non-empty; no continuing script output with the same
  `reference_signature`. Tagged outputs at `buyer_return_address ?? buyer` must total `>= collateral_return_lovelace`.
  If `seller_return_address` is `Some`, tagged outputs there must be `>= input.value - collateral_return_lovelace`
  (per-asset `>=`). If `None`, nothing constrains the seller's share (seller signature is the only gate).
- `WithdrawRefund` (refund): state in {FundsLocked, RefundRequested, RefundAuthorized}; buyer signs; `result_hash` empty;
  `lower >= submit_result_time` unless state is `RefundAuthorized`. If `buyer_return_address` is `Some`, tagged outputs there
  must be `>= input.value` (full escrow incl. collateral). If `None`, unconstrained.
- **No protocol fee of any kind** (README L14, state_machine_diagram "does not enforce a protocol fee"; validator params are
  only `required_admins_multi_sig`, `admin_vks`, `cooldown_period`, L96-100).
- Script outputs are parsed strictly (`expect new_datum: Datum`, L196-203): any dust UTxO at the script address in the same
  tx aborts it; batching several escrow inputs in one tx is allowed if their `reference_signature`s differ (L166-181).

What the Masumi node actually builds (collection/service.ts L199-281; collect-refund/service.ts L171-209;
withdrawal-outputs.ts L29-83): payout = 1 escrow input (+ seller wallet inputs for the ledger fee) and outputs
(a) tagged seller output at `datum.seller_return_address ?? request.sellerReturnAddress ?? wallet.collectionAddress ??
wallet.walletAddress` carrying ALL escrow assets minus `collateral_return_lovelace`, **topped up to the min-ADA of the
serialized output** when smaller (`addMinimumAdaOutput`), (b) tagged collateral output at `datum.buyer_return_address ??
buyer` with exactly `collateral_return_lovelace` (if > 0), (c) untagged change to the seller wallet; `fee: null` always.
Refund = tagged output at `datum.buyer_return_address ?? request.buyerReturnAddress ?? collectionAddress ?? walletAddress`
carrying the full escrow value (`collectAssets = utxo.output.amount`, `collateralReturn: null`), plus buyer-wallet change.
The node also emits batch versions (`generateMasumiSmartContractBatchWithdrawTransactionAutomaticFees`) with N escrow inputs.

Observed on Preprod (Koios `tx_info`, 2026-10-07, all addresses base addresses, ADA only):
- Payout `1b0cdc319bd4c4799cc2e2713156d3cf8cf97327761a862dd7fa72008927ad52`: inputs seller wallet 5,000,000 + escrow
  4,503,950 (datum state 1, collateral 4,469,470, return addresses None/None); outputs: 1,383,510 to seller wallet TAGGED
  (`Constr 0 [escrow tx, 0]`; true share was 34,480, topped up to min-ADA), 4,469,470 to buyer TAGGED, 2,976,293 seller
  change untagged; fee 674,677; redeemer `Constr 0 []`. Same pattern in `b2936aa75a2f787041712bc78e062e34d3bfe0587143d03480f694ab1ad70e15`.
- Refund `e93565c582377e09278d4fe6d30205cf1a9f03980a22561699af00cee81b9045`: buyer wallet 5,000,000 + escrow 4,469,470
  (state 0, no result); outputs 4,469,470 to buyer TAGGED + 4,344,186 buyer change; fee 655,814; redeemer `Constr 3 []`.
- Batch refund `3df28ea39f81593567bb1de0dc015ea3f81bfa0bafb54cd16a77d2121ef9344f`: 2 escrow inputs (`96d098bd…#0`, `#1`),
  2 spend redeemers (both `Constr 3`), one tagged output per input (tags `[96d098bd…, 0]` and `[…, 1]`) plus change.
  Note: no fee-wallet output in any V2 tx.

## 5. Differences from V1 that matter for `_datum_matches` / `_strict_values` / `_redeemer_matches`

1. Datum arity: `_constructor(datum, 0, 16)` → 19 for V2; indices shift: buyer 0, seller **2**, bytes at 4,5,6,7,8,10,11,
   collateral **9**, times **12-15**, cooldowns 16-17, state **18**. Two new `Option<Address>` fields (1, 3) must be
   validated as `Constr 0 [Address]` or `Constr 1 []`.
2. Identifier: V2 compresses `seller_nonce+agent_identifier` as the first segment and (probably) appends the script address
   as a 5th segment (section 1). `reference_key`/`reference_signature` are long CBOR blobs (42 / 217 bytes live), fine for
   lz-string but the ASCII alphabet of `_identifier()` must include the Bech32 address if the 5th segment is present.
3. States: accept constructors 0-5. Payout can legitimately spend from state **4 (`WithdrawAuthorized`)** as well as 1;
   refund can spend from 0, 2 or **5 (`RefundAuthorized`)**. `result_hash` can be cleared by `AuthorizeRefund`, so a job
   that once showed a result hash can still end in a refund (state 5, empty hash).
4. Fee policy: V1 enforced `fee_permille` to `fee_address` on-chain and our `_strict_values` requires
   `protocol_fee = max(1435230, locked*permille//1000)` to the fee wallet. V2 has NO fee output; the payout check must
   require zero value to any fee address (and `settlement_policy` fee fields are irrelevant for V2 sources).
5. Seller payout amount: V1 check is `collected == locked - protocol_fee`. V2 node pays `locked - collateral` but tops the
   output up to min-ADA from the seller's own wallet inputs, so the exact-equality form becomes
   `collected >= locked - collateral` with the surplus explained by `seller_change - seller_inputs == -(collected - (locked - collateral)) - collateral_out - fee`
   (conservation already covers this via `_conserved`). Observed: 1,383,510 paid for a 34,480 share.
6. Payout recipient: when datum field 3 is `Some(addr)` the chain itself guarantees the tagged output lands at exactly that
   full address; use it as `payout_address` (and reject a job whose snapshot `payoutAddress` differs). When `None`, the node
   sends to its collection/wallet address and the chain does not bind it — our existing "payout_address or seller_address"
   logic applies, but mark the proof weaker.
7. Collateral recipient: V1 sends collateral to the buyer wallet; V2 sends it to `buyer_return_address ?? buyer`. Refund
   recipient likewise `buyer_return_address ?? buyer`. `_strict_values` must compare against the effective return address,
   and reject datums where the effective buyer and seller targets coincide (README L136-141; double-satisfaction).
8. Tag datums: V2 payout/refund outputs carry inline datums (`Constr 0 [bytes, int]`), so any "outputs must have no datum"
   assumption is wrong; conversely the tag can be used as a strong link from output to the spent escrow input. Blockfrost
   `txs/{hash}/utxos` exposes `inline_datum` (CBOR hex) per output; decoding the tag means parsing
   `d8799f58 20 <32B> <uint> ff` (indefinite-length Constr 0). UNVERIFIED: exact CBOR bytes mesh emits (definite vs
   indefinite list) — decode generically or compare via Blockfrost `scripts/datum/{hash}` when `data_hash` is present.
9. Redeemers: indices 0 and 3 are unchanged; `_redeemer_matches` requiring exactly one `spend` redeemer will reject V2 batch
   payouts/refunds (observed 2-input batch above). Also `redeemer_data_hash` for `Constr 0 []`/`Constr 3 []` is the same
   as in V1. Dispute settlements (index 4, state 3) are a third terminal kind our verifier does not classify.
10. Script identity: the escrow address is `addr_test1wzs4e6wc…` / script hash `a15ce9d8…` (PlutusV3); the blueprint hash
    differs because of parameters — do not compare against `plutus.json` `hash`.
11. Times are POSIX milliseconds on-chain, as in V1 (our `stamp*1000` upgrade of second-precision inputs still holds).
12. Integers: Masumi rejects values > 2^63-1 (`MAX_DATUM_INT`) off-chain; the validator does not, so bound them ourselves.
13. V1 payment key only vs V2 return address: `address_to_verification_key` is applied only to fields 0 and 2; fields 1/3
    may be script addresses on-chain (not produced by the current API) — `_full_address_matches` must tolerate header
    types other than 0/6 only if we decide to accept such datums; recommend rejecting (header nibble not in {0, 6}).

## 6. Not verified / open

- UNVERIFIED: which identifier form (4 or 5 segments) Sokosumi/the Masumi node use for V2 `blockchainIdentifier` in `/start_job`.
- UNVERIFIED: `WithdrawDisputed` branch internals (vested_pay.ak L506-642) were only read via README; irrelevant to payout/refund proofs.
- UNVERIFIED: exact CBOR encoding of the `OutputReference` tag datum (see item 8); Blockfrost JSON for it should be
  `{"constructor":0,"fields":[{"bytes":…},{"int":…}]}` (Koios shows exactly that).
- Verified live but only on ADA-only, single-input txs: the min-ADA top-up size (1,383,510 at `coinsPerUtxoSize = 4310` for a
  tagged base-address output) will differ for outputs with native tokens.
