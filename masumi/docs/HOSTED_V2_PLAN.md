# Hosted V2 implementation checklist

The public registration service is deployed. This checklist covers enabling real Preprod execution; it is not implemented merely by deploying the current `hosted_api` module.

## 1. Resolve managed registration (Masumi operator dependency)

- Reconcile the existing SaaS agent and registry record, never create a duplicate to retry.
- Ask Masumi to inspect managed registration funding/worker processing using the support note.
- Require RegistrationConfirmed and the same nonempty agent identifier in both APIs.
- Independently check the mint on Blockfrost and re-read source-local Dynamic pricing.

The completion API's funding message is not a root-cause diagnosis. The canonical registry already contains the requested Dynamic pricing; do not rewrite it based on the SaaS display mismatch.

## 2. Implement a separate hosted V2 escrow adapter (Ezra)

Preserve the tested local V1 adapter and its databases. Do not relax its localhost or V1 identity checks to make hosted traffic pass.

| Boundary | Current local V1 | Hosted API evidence / required V2 work |
| --- | --- | --- |
| Authentication | local node `token` header | SaaS `x-api-key`, fixed HTTPS host, no credential-bearing redirects |
| API base | localhost `/api/v1/` | `https://app.masumi.network/pay/api/v1/` |
| Source discriminator | `paymentType: Web3CardanoV1` | `paymentSourceType: Web3CardanoV2` |
| Agent identity | local registry NFT and seller | hosted confirmed NFT plus hosted recipient/seller custody |
| Dynamic fee | fixed registration amount | reviewed `RequestedFunds`, native ADA unit `""`, exact integer lovelace |
| Payout | local wallet collection address | review `sellerReturnAddress` semantics; verify it binds the approved payout in the V2 datum and settlement |
| Source checks | V1 source + selling-wallet details | V2 contract/network plus scoped custody evidence; hosted source listing omits selling wallets |
| Evidence | V1 decoder and fee/collateral accounting | independent V2 datum/redeemer/result/payout verification; do not reuse V1 decoding by assumption |

The hosted OpenAPI exposes create-payment, submit-result, authorize-refund, and read/diff routes. Their presence is not proof of SDK compatibility or correct settlement. Pin the live schema, inspect the corresponding V2 contract/payment-node implementation, and test the exact response/state machine before enabling writes.

Acceptance conditions: no payment before approved cart; explicit test-ADA amount and payout; funds locked before checkout; uncertain writes reconcile rather than repeat; no payout from unconfirmed merchant evidence; no automatic refund after partial/charged evidence; authoritative V2 settlement verification.

## 3. Deploy execution infrastructure (Ezra)

- Use a dedicated service configuration, separate from the current read-only publication module.
- Persist jobs/checkpoints on an attached volume with one worker, or migrate storage to a transactional shared database before scaling replicas.
- Require an operator/caller credential for jobs, approval, status and evidence. Public schema/health must not expose customer records.
- Store only required scoped secrets in Railway variables; do not upload wallet recovery, local node admin credentials or repository `.env` files.
- Define client authorization for Masumi callers explicitly; account API keys and caller tokens serve different purposes.
- Test crash/restart between writes and durable checkpoints before enabling the worker.

## 4. Real Preprod acceptance (Ezra)

Run simulated checkout success → real V2 payout and definitive no-purchase failure → real V2 refund. Record exact transaction hashes, escrow inputs, datum bindings, payout address and amounts. Respect actual contract release/dispute times. Passing mocked tests or a hosted registration does not count as this acceptance.

## 5. AgentCard handoff (Mason, then Ezra)

Mason provides the staged v2 prepare/confirm/inspect adapter, saved quote/cart IDs and full charge ceiling, authoritative order evidence and conservative unknown/partial outcomes. Coordinate current credentials and exclusive rotating-token ownership. Ezra wires the adapter after the escrow checks pass. A specific real-card purchase still requires its item, delivery details and spending ceiling to be approved.

## Evidence sources

- [Hosted registration implementation](https://github.com/masumi-network/masumi-saas/blob/main/apps/web/src/lib/agent-registration.ts)
- [Completion route and generic pending message](https://github.com/masumi-network/masumi-saas/blob/main/apps/web/src/app/api/agents/%5BagentId%5D/complete-registration/route.ts)
- [Live hosted OpenAPI](https://app.masumi.network/api/openapi)

Local diagnostic snapshots are ignored under `work/`; inspect current responses again before executing a payment.
