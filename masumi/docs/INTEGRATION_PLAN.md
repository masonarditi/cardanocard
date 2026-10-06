# Cardano Card — current integration contract

Updated 6 October 2026, Singapore time. This supersedes earlier Base/x402 and issuing-first demo plans. Work stays local on `ezra`; keep Blockfrost Preprod. Mason owns `agentcard/`, Ezra owns `masumi/`. Mason's legacy `purchase()` has not been changed.

## Demo architecture

Prompt → prepare cart without spending → approve saved quote → create Masumi escrow request → buyer locks test ADA → verify matching funds → confirm the approved Vault cart → inspect authoritative merchant outcome → save result → submit result hash → wait for contract windows → verify collection to the designated payout address.

Masumi escrow is a smart contract; sending ADA directly to the payout wallet does not fund a job. The seller signing wallet and its collection address are different roles. The native-ADA wire asset ID is the empty string `""`; amounts are integer lovelace. USD amounts are integer cents. The first demo uses a fixed 10-test-ADA price matching registration, separately from the merchant authorization ceiling. No USD conversion or real-dollar reimbursement is claimed for test ADA. Base/x402, issued cards, conversion and dynamic reimbursement pricing remain deferred.

## Implemented on Ezra's side

- `StagedEngine` reserves a stable job before cart preparation, requests explicit approval bound to saved quote terms, and creates no escrow until approval. Status exposes item descriptions/quantities, estimates, ceiling, expiry, fixed test-ADA price and recipient.
- Escrow creation must return exactly the approved amounts and payout address. Invalid terms are withheld from funding responses. Changed inputs, stale approval, expired quotes and changed recipients cannot start checkout.
- The saved confirmation marker precedes provider I/O. Unknown preparation/confirmation resumes through inspection; no automatic repeated cart creation or card confirmation.
- Confirmed merchant outcomes must match job, quote, conversation, cart, Vault source, currency and ceiling. Partial/charged evidence is sticky: a later no-purchase claim cannot erase it and trigger a refund.
- Definitive no-purchase failure enters the existing buyer-request/seller-authorize refund workflow. Uncertain outcomes require reconciliation. Provider approval is surfaced; continuation only inspects an already attempted confirmation. A provider that requires another confirm after approval needs an explicitly reviewed continuation contract before enabling that path.
- Payout routing is validated against public Masumi configuration before payment creation, during observation and before result submission. Each payment snapshots `payoutAddress`; setup and acceptance bind it on resume.
- The independent Blockfrost verifier distinguishes the seller signing address from the collection address and checks custom-recipient settlement, fees and collateral.
- Terminal commands show quote approval, test ADA, payout address and release timing. A node-reported payout is explicitly not independent chain proof.

## Mason v2 handoff

The integration module must declare `PURCHASE_PROTOCOL_VERSION = 2` and export synchronous functions (our boundary calls them in a worker thread):

```python
prepare_purchase(*, job_id, intent, max_total_usd, address)
confirm_purchase(*, job_id, quote_id)
inspect_purchase(*, job_id)
```

`max_total_usd` is decimal text. `prepare_purchase` must not spend. Both writes deduplicate by job and saved quote; `inspect_purchase` must not create a cart or confirm checkout. Provider I/O must be bounded inside the module. Importing the module must have no network or purchase side effects.

Prepared response (exact schema in `staged_purchase.PreparedQuote`):

```json
{
  "status": "prepared", "job_id": "saved-job", "quote_id": "saved-quote",
  "conversation_id": "provider-conversation", "cart_hash": "provider-cart-hash",
  "currency": "USD", "payment_source": "vault",
  "subtotal_cents": 600, "estimated_total_cents": 750,
  "authorization_ceiling_cents": 900, "expires_at": 1791260000,
  "merchant": "Example merchant", "items": [{"name": "Example item", "quantity": 1}]
}
```

`expires_at` is a future Unix timestamp in seconds; the sample value is illustrative. Items contain only `name` and positive integer `quantity`. All three amounts are required. Missing, inconsistent or over-budget values stop approval/funding.

Checkout/inspection responses use `CheckoutOutcome` with these common identity fields: `job_id`, `quote_id`, `conversation_id`, `cart_hash`, `payment_source: vault`, `currency: USD`.

- `confirmed`: requires `merchant_confirmed: true`, `order_id`, `merchant`, nonempty `items`, integer `total_cents` within the approved ceiling and `charge_status: authorized|captured`. A bare order ID is insufficient.
- `failed_no_purchase`: requires `no_purchase: true`, `charge_status: none`, no order ID and no previously observed partial/charged outcome.
- `pending`, `partial`, `needs_input`: preserve uncertainty; no automatic refund or new confirmation.
- `approval_required`: may include an HTTPS `approval_url`; complete provider approval then inspect the same purchase. No repeat-confirm assumption.

Only explicitly defined fields are accepted; sanitize provider responses before returning them. Keep raw credentials, PAN/CVC and provider debug bodies out of responses. Supply sanitized fixtures for uncertain, partial, changed-price and confirmed outcomes before live connection. Restore the intended AgentCard environment privately, reconcile the earlier gum attempt and coordinate exclusive rotating-token use. Do not rerun vault enrollment for an already-linked card.

## Configuration and execution

Local rehearsal: `PURCHASE_BACKEND=staged_fake`, `CARDANO_CARD_MODE=local`, dedicated `CARDANO_CARD_DB`. Both payment and purchasing are simulated. Existing fake/replay/acceptance paths remain available independently.

Real escrow: use setup-generated `.env.preprod` values, `CARDANO_CARD_MODE=preprod`, `MASUMI_V1_COMPATIBLE=true`, matching `MASUMI_FEE_LOVELACE` and `PAYOUT_ADDRESS`. The application reads `.env`/process environment; the acceptance CLI reads `.env.preprod` explicitly. Never shell-source unreviewed secret files. Registration and restricted runtime keys must exist first.

Mason module is opt-in: `PURCHASE_BACKEND=staged_module`, `MASON_STAGED_MODULE` set to the reviewed module, `ALLOW_EXTERNAL_VAULT_CHECKOUT=true`, `VAULT_OPERATOR_EXCLUSIVE=true`, and real Preprod escrow. These flags do not establish provider readiness or authorize an unspecified purchase. Defaults leave external checkout disabled. Use one worker and a dedicated database. Never start the staged runner against old legacy jobs; it will leave them untouched for their original runner.

## Local payout configuration

A dedicated Preprod receiving wallet was generated and recovery independently re-derived. Recovery remains in ignored `data/payout-wallet/recovery.json` with owner-only permissions; no seed is needed in Masumi. The local seller now has that address as `collectionAddress`; `.env` contains the matching `PAYOUT_ADDRESS` and the infrastructure seed configuration records it for a future empty database.

Pinned Masumi 0.22.0 has no API to update an existing wallet's collection address. We applied a narrowly scoped local Prisma transaction only after checking the seller had zero payment and registration records and no lock/pending transaction. The prior collection value is backed up in ignored `data/payout-wallet/collection-migration-backup.json`. Wallet ID and signing key are unchanged; the public payment-source API verified the new destination. Do not replace/delete the seller, recreate the database, or repeat this migration on an active seller. No chain transaction was submitted.

## Remaining live acceptance

1. Registration and restricted-key configuration are complete. The buyer has 105 and seller 314.749890 test ADA after the mint. The registered local API runs on port 8081 with simulated purchasing.
2. Prove real escrow success/payout and decline/refund with simulated purchasing using `PREPROD_ACCEPTANCE.md`; retain independent transaction evidence.
3. Connect Mason's reviewed v2 module and test the contract with fixtures. Reconcile old sandbox state and verify current credentials privately.
4. Exercise sandbox failure/refund. Sandbox cannot prove a real merchant order.
5. Run one specifically approved real-card purchase with Preprod escrow. Verify merchant order and on-chain payout separately; allow for the contract release windows. No instant payout promise.

No public hosting, multiple customers, token handoff, live card charge or successful on-chain settlement is implied by passing local tests. Keep the single authorized demo caller until customer ownership/authentication is implemented.
