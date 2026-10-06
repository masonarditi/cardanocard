# Integration build status — 6 October 2026

## Hosted registration deployment

Railway deployment `031ec0b3-874f-41ef-bca0-c363995476e8` succeeded in eztramble's Projects. Public health and schema are verified at https://cardanocard-preprod-production.up.railway.app. This endpoint explicitly disables jobs; it has no payment/card credentials or connection to the private local runtime. Masumi accepted hosted Preprod agent `544fcfcd-29e5-4453-9a04-ac6554956d56` under ezzycoin. Both SaaS and registry remain RegistrationRequested, with no transaction, agent identifier or processing timestamp. Canonical registry pricing is correctly Dynamic; only the SaaS copy displays Free. The completion API's funding message is generic, although independent Blockfrost checks found no history for either hosted registration wallet. Do not fund or recreate the registration blindly. The read-only readiness CLI verifies identity/payout/source/pricing and distinguishes registration from execution readiness. **341 tests pass.** See [hosted registration](docs/HOSTED_REGISTRATION.md), the [V2 implementation checklist](docs/HOSTED_V2_PLAN.md), and the [unsent support note](docs/MASUMI_REGISTRATION_SUPPORT.md).

## Current stage

The local integration and Preprod acceptance tooling are implemented. The staged quote/approval/Vault boundary is implemented and both local terminal rehearsal paths pass. Wallet funding and on-chain registration are confirmed; real escrow payout/refund acceptance remains to be executed. No real escrow payout/refund, merchant order from this iteration or completed sandbox confirmation has been demonstrated.

Work is in `masonarditi/cardanocard`, branch `ezra`, under `masumi/`. The archived `cardano-card` directory is reference only. The new workspace has its own virtual environment and restored private integration configuration. Existing node containers and their encrypted database volume were preserved. AgentCard credentials and rotating user tokens were deliberately not copied into the new clone.

## Implemented

- Durable job coordinator, immutable input/result records, one-process SQLite locking and lifecycle events. Checkpoints precede external writes; uncertain writes require reconciliation instead of automatic retries.
- AgentCard bridge with cart persistence, explicit USD evidence, maximum-of-total/estimate/authorization-ceiling budget checks, approval continuation and conservative handling of partial or unknown orders.
- Sandbox transport with organization-mode verification, authenticated merchant currency metadata, exclusive operator handoff, local token lock and durable refresh protection. No vault enrollment.
- Readable live terminal feed with color, emoji and ASCII mode, plus private evidence exports.
- Gated V1 Preprod buyer funding/refund and seller escrow adapters, bound to the exact request, funds, keys, contract and deadlines.
- Resumable setup CLI: reviewed OpenAPI verification, wallet/balance inspection, matching registration reuse, restricted buyer/seller key creation and atomic private `.env.preprod` output. Unknown setup writes are not repeated.
- Acceptance runner for real Preprod escrow with simulated success/payout, simulated decline/refund, or actual AgentCard sandbox/refund. Stable IDs, one request per database and existing funding checkpoints protect resume paths.
- Independent Blockfrost evidence verification: exact compressed payment identity, datum/amount/deadline/result binding and strict native-ADA settlement checks. Supported unbatched settlement checks include wallet credentials, script spend redeemer, conserved value, fee/collateral distribution and recipient attribution. Unsupported shapes remain incomplete.
- Runtime restoration tool preserves original node encryption/admin/database settings, skips locked databases, excludes AgentCard credentials and refuses overwrites.

## Verification and environment

**347 automated tests pass** in the new workspace; these include mocked node/AgentCard HTTP responses and synthetic chain fixtures. These tests do not substitute for live settlement evidence. Dependency checks pass and package imports resolve to the new clone.

### October 6 chain diagnosis

The current `ezra` HEAD matches fetched `origin/main` at `b70af9b`; local changes remain uncommitted. Live preflight passed: reviewed schema, confirmed registration, fixed fee, wallet identities, collection destination and separate restricted keys. Blockfrost reported epoch 317, protocol 11, and a latest block 42 seconds old. Both installed serializer files match the patch manifest and current cost models are unchanged. Buyer: 105 test ADA; seller: 314.749890; payout: zero. Private evidence: `work/chain-diagnosis-20261006.json`.

The first real payout attempt received HTTP 400. The pinned fixed-price V1 endpoint explicitly rejects the `Amounts` field our buyer supplied. The buyer now omits that field while retaining the exact budget check. HTTP status and safe diagnostic categories persist without provider bodies; unknown writes remain non-retryable. Acceptance stops for reconciliation, saving incomplete evidence, when funding is unconfirmed and no buyer record exists. Six regression cases cover the fixed-price contract, private durable error diagnostics and preventing checkout after absent funding.

Read-only reconciliation of job `e5ff93ac-3c3f-451a-87e9-c938ee59f2c8` found buyer HTTP 404, no seller current transaction or transaction history, and unchanged balances. No funding or settlement is recorded. Original deadlines expired; do not replay its payload. Real payout/refund acceptance still needs fresh, separately recorded cases after reconciliation. No new funds or merchant purchases were initiated during diagnosis.

Read-only checks confirm the local Masumi Payment Service and PostgreSQL are running. Its 0.22.0 OpenAPI matches the pinned source fingerprint. The reviewed SDK is 1.2.0. One Preprod purchasing wallet and one selling wallet exist; the latest read-only check reports **105 test ADA in the buyer wallet and 314.749890 test ADA in the seller wallet after registration**. Cardano Card Preprod registration is confirmed and independently verified through Blockfrost. Separate capped buyer/seller keys and the agent identity are saved in ignored `.env.preprod` (0600). Schema compatibility is a wire-format gate, not settlement proof.

## Actual sandbox evidence retained

- Coffee job `f5db882e-111a-4d3d-87cf-58fd09a80491`: an actual sandbox cart reported a 1,241-cent estimate but a 2,348-cent authorization ceiling against the approved 2,000-cent cap. Our bridge rejected it and completed a **simulated** fee refund. Zero confirmations, no merchant order and no Cardano transaction.
- Gum job `81efeedd-26a1-4ab4-88c4-7d9be7beaeb2`: cart request remains uncertain, with no usable conversation ID. Saved state is `reconciling` / `cart_requested`, with zero confirmations. Resolve it using provider logs before any new sandbox checkout; the new combined acceptance runner blocks while this unresolved record remains.

The paused sandbox database and private evidence were restored into the new workspace. Current token custody still requires coordination with Mason; historical exclusive handoff does not establish a new concurrent operator arrangement.

## Current staged escrow iteration

The demo now uses Cardano Preprod escrow plus Mason's existing Vault card. Base/x402, conversion and new-card issuance are deferred. `docs/INTEGRATION_PLAN.md` defines the implemented v2 handoff and defaults. Mason's `agentcard/` implementation was not modified.

- Prepared quotes expose items, full USD ceiling, fixed test-ADA price, expiry and payout destination. Approval precedes escrow creation; funding precedes confirmation.
- Confirmation attempts are checkpointed before I/O. Unknown writes inspect the same job; partial/charged evidence cannot later become an automatic no-purchase refund.
- A dedicated Preprod receiving wallet is generated and verified, recovery is ignored/owner-only, and the existing local seller's collection address is configured to it. Wallet ID and signing key are unchanged. Public node inspection confirmed the setting. No chain transaction was submitted for this change.
- Payout configuration is checked against saved job terms before payment creation, observation and result submission. Setup and independent custom-recipient proof verification are wired to the same address.
- Manual terminal quote approval and read-only status show payment terms and node-reported versus independently verified settlement. Both local HTTP/terminal rehearsals passed: success -> simulated payout and definitive decline -> simulated refund. Evidence is private under `data/staged-rehearsal-*.json`.
- Blockfrost Preprod is active in the running node and an authenticated latest-block read passed. Corrected a malformed local endpoint URL; NOWNodes is not active.
- External Vault checkout remains disabled by default. Current authenticated AgentCard readiness and the combined real merchant/escrow flow remain unproven; receive Mason's v2 module and current private credentials before enabling it.

## Next actions

1. Registration/configuration is complete. Local API `http://127.0.0.1:8081` is running with real Preprod escrow and simulated purchasing; no job has been started on that runtime.
2. Complete real escrow success/payout and decline/refund using explicitly simulated purchasing. Preserve transaction hashes and independently verified evidence. Allow for real settlement windows.
3. Reconcile the unresolved gum attempt with Mason, receive current matching sandbox files and an exclusive handoff, then run `sandbox_mode` → real Preprod refund.
4. Connect Mason’s staged adapter with reviewed fixtures and a specifically approved real-card demo purchase.
5. After core acceptance: external client/MIP-003 conformance, public discoverability and caller authorization. Hosted access, multiple customers and x402 remain separate unfinished scope.

See [terminal acceptance runbook](docs/PREPROD_ACCEPTANCE.md) for exact commands and completion criteria. Current work remains local; no production purchasing is enabled.

## Confirmed registration

Using the official Masumi skill and pinned self-hosted API, registration `cmuwcob4i001dry7mj91qzkcn` reached `RegistrationConfirmed` on Preprod. Name: Cardano Card Preprod; API: `http://127.0.0.1:8081`; fixed price: 10 test ADA. This is a local test identity, not public marketplace reachability.

Transaction: `43d22b6b7e20530a65504855486d65d18e8dc7284b226145b33ea4dd3c4bec01`; fee: 0.250110 test ADA. Blockfrost confirmed the minted NFT quantity of one, initial mint transaction and ownership by the configured seller. The collection destination remains the dedicated demo receiving wallet.

The original registration attempt was definitively rejected due to old Mesh cost models. A reversible Preprod-only serializer patch was verified offline in both ESM/CJS and applied to the existing container. The same failed request was reconciled and retried once; no duplicate agent was created. See `docs/LOCAL_MASUMI_SETUP.md` for the runtime patch and recreation limitations. Evidence is saved privately in `work/preprod-registration-confirmed.json`.
