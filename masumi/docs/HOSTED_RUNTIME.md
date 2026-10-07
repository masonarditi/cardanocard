# Hosted runtime on Preprod (no local node)

> **16:45 SGT:** Sokosumi Preprod listed **the V2 SaaS agent** (CardanoCard tUSDM, `/v3`, `…36cbc1000000`, 2,000 credits)
> after a manual edit on the Masumi side — not the V1 one. The runtime is therefore back on the hosted V2 rail
> (`scripts/railway_rail.py --rail v2`, job DB `/data/jobs-v2.db`). The V1 node + agent (`/v4`) stay deployed as the
> fallback (`--rail v1`); a funded V1 run through the hosted node completed on 2026-10-07 16:27–16:32 (job `75ad8d3a…`,
> FundsLocked → sandbox cart → sandbox_mode → RefundRequested, buyer = our local node via `preprod_buyer --remote-agent`).

> **Current architecture (2026-10-07 16:20 SGT):** the Railway runtime serves **CardanoCard on the V1 rail** (`…/v4`,
> identifier `7e8bdaf2…8589cf963cabcd`, Fixed 20 tUSDM) through **our own Masumi payment node on Railway**
> (`cardanocard-node`, image 0.22.0 + the Preprod cost-model patch, Postgres plugin, schema `v22`), using our funded
> seller wallet `62f4…` (collection → `addr_test1qpgq…`). Reason: the Preprod registry service that Sokosumi reads
> indexes only the V1 policy and rejects V2, while the Masumi SaaS mints only V2 — so a Sokosumi-visible agent must be
> minted from a V1 node we control. The V2 agents below remain registered but idle. Node details:
> `work/node-domain.txt`, registration receipt `work/node-registration-v1-submission.json`. Gotcha: `.env.preprod`
> values are single-quoted; copy them with python-dotenv, not `cut`, or Railway stores the quotes.

Since 2026-10-07 the public Railway service runs the **real** agent, not the registration stub:

- URL: https://cardanocard-preprod-production.up.railway.app (Railway project `cardanocard-preprod`, service
  `0f86cbfb…`, volume `cardanocard-preprod-volume` at `/data` for `jobs.db`)
- Agent: **Cardano Card Preprod** `67ab0c92c4ac1610895a1c965ee50aba41a8f1513b15240723b3bd0b113c4cc308fac5e3056f6ad27c1219443a800f3a3911fb7bc4961c38c6000000`
  (RegistrationConfirmed, Dynamic pricing, 10 test ADA per job)
- Escrow rail: Masumi's hosted payment service `https://app.masumi.network/pay/api/v1/` (`x-api-key`), payment source
  **Web3CardanoV2**, contract `addr_test1wzs4e6wc95hkwezlccjw9mdvq0r0rsgx6zk34avptga3ftgn37w4g`
- Selling wallet (signs result/withdraw, funded by Masumi with 110 tADA): `d5e0d3b0…` / `addr_test1qr27p5as…`.
  This is what the hosted service puts on every payment and what `/start_job` returns as `sellerVKey`; the registry
  record's `SmartContractWallet` (`ebef83fc…`) is a different wallet and must not be configured as the seller.
- Payout (`sellerReturnAddress`): `addr_test1qpgq4gf9fmzg3gkdnp9jdtujtscr2qdms07tmtp8aqz5ykc7uvlelkeuqsgctx9n4en6xmkf4qhqkumzcc3qwt4845hqsxwadj`
- Purchasing: `PURCHASE_BACKEND=mason` → `agentcard/purchase.py` in **sandbox** (since 2026-10-07 ~10:55 SGT): a real
  Amazon cart is built, the confirm is declined with `sandbox_mode`, the buyer is refunded. Sandbox org credentials and
  a linked sandbox user (`usr_3c01d0a5…`) live only in Railway variables + `/data/agentcard` (`AGENTCARD_HOME`); the
  local copy is parked as `agentcard/.agentcard_tokens.handed-off.json` and must not be used while Railway holds it.
  Switching to the real card = `AGENTCARD_ENV=prod` + prod org creds + a prod user token (Mason's phone code).
- Escrow per job = `max_total_usd` × `MASUMI_LOVELACE_PER_USD` (1 tADA per USD), floor `MASUMI_FEE_LOVELACE` (2 tADA),
  cap 100 tADA — so the payout reimburses the card that fronted the purchase.

## What is verified live (2026-10-07)

- `POST /start_job` without a token → the runtime creates a payment on the hosted service and returns MIP-003 terms
  (`blockchainIdentifier`, times, `sellerVKey`, `RequestedFunds`, `smartContractAddress`, `rail: hosted-v2`).
- `GET /status` → `awaiting_payment` / `AwaitingPayment`; repeating a request id returns the same job.
- `/jobs`, `/evidence`, `/events` → 401 without the bearer token (`CARDANO_CARD_TOKEN`, in `.env.hosted`).
- The hosted `/payment` API requires `supportedPaymentSourceIndex` and `RequestedFunds` for V2 Dynamic agents; the
  adapter sends index 0 (the agent's only source).

## Verified against the payment-service 0.29 source (2026-10-07)

- **Refunds on V2:** the seller's `POST /payment/authorize-refund` is accepted while `onChainState` is `RefundRequested`
  or `Disputed` (`src/routes/api/payments/authorize-refund/index.ts`), and the buyer's node collects from
  `RefundAuthorized` immediately — or on its own from `RefundRequested`/`FundsLocked` once `submitResultTime + 10 min`
  has passed with no result (`packages/payment-source-v2/.../purchases/collect-refund/service.ts`). So the engine's
  behaviour on the hosted rail (authorize as soon as the buyer requests) is valid and only speeds the refund up.
- **Sokosumi's paid-job schema** (`packages/masumi/src/schemas/agent/start_job.schema.ts`) needs `id`, `input_hash`,
  `identifierFromPurchaser`, `blockchainIdentifier`, the four times as integers, `agentIdentifier`, `sellerVKey`, and
  optionally `paymentSourceType` / `supportedPaymentSourceIndex`; `/status` needs `status` ∈ awaiting_payment |
  awaiting_input | running | completed | failed with `result` on completed. Both are covered by tests.
- **Purchaser nonce:** the payment service takes 14–26 hex chars; `identifier_from_purchaser` accepts that range.
- **Deadlines:** hosted default `MASUMI_DEADLINES_MIN=12,30,45,60` (payBy, result, unlock, dispute) so a marketplace
  buyer's node has 12 minutes to land the funding tx; payout therefore lands ~45–50 min after the hire.

## Operator tooling

- `GET /diagnostics` (bearer token; `?probe=true` also calls the hosted payment source and Agentcard) — shows the wired
  rails, the Agentcard user on the volume and whether its token refreshes. Verified on Railway: `agentcard_probe 200`.
- `GET /operator/jobs/{job_id}` (bearer token) — the full stored job.
- `python -m cardano_card.hosted_evidence --job JOB_ID` — pulls that job and verifies its transactions through NOWNodes
  (or Blockfrost); writes `work/hosted-evidence/JOB_ID.json`. Strict V2 datum decoding is pending (`docs/V2_DATUM_NOTES.md`).

## Code

- `src/cardano_card/hosted_escrow.py` — `HostedMasumiEscrow` (same engine contract as the local V1 adapter).
  V2 `WithdrawAuthorized`/`RefundAuthorized` are mapped onto `ResultSubmitted`/`RefundRequested`; the engine asks
  for refund authorization (`automatic_requested_refund = False`) until a live run shows the hosted node collecting.
- `api.py`: `CARDANO_CARD_MODE=hosted`, `CARDANO_CARD_PUBLIC_JOBS=true` (MIP-003 routes open; registry buyers carry
  no token of ours), `CARDANO_CARD_HOST=0.0.0.0`, `PORT`.
- `Dockerfile.hosted` + `railway.hosted.json`. Railway refuses a Dockerfile `VOLUME`; the volume is attached in
  Railway. The container runs as root because Railway mounts volumes root-owned.

## Deploying

```sh
S=/private/tmp/cardanocard-hosted-runtime-$(date +%Y%m%d); mkdir -p "$S"
cp pyproject.toml README.md "$S/"; rsync -a --exclude __pycache__ src "$S/"
cp Dockerfile.hosted "$S/Dockerfile"; sed 's/Dockerfile.hosted/Dockerfile/' railway.hosted.json > "$S/railway.json"
cd "$S" && railway link --project b7379319-a841-459c-ae44-e1335c7eee89 --environment 2a526083-0e1d-42f9-9605-274c57bce885 \
  --service 0f86cbfb-9a3f-4419-b579-253f2c36fb07 --workspace "eztramble's Projects"
railway up --ci --service cardanocard-preprod --workspace "eztramble's Projects"
```

Variables are set on the service (`railway variables`): `PAYMENT_SERVICE_URL`, `PAYMENT_API_KEY` (SaaS key from
`.env.hosted`), `CARDANO_CARD_TOKEN`, `AGENT_IDENTIFIER`, `SELLER_VKEY`, `PAYOUT_ADDRESS`, `MASUMI_FEE_LOVELACE`,
`MASUMI_PAYMENT_SOURCE_INDEX`, `PURCHASE_BACKEND`, `FAKE_SCENARIO`, `CARDANO_CARD_*`. Never upload `.env*`, wallet
recovery or Agentcard tokens in the staging directory.

## Sokosumi listing (why a second, Fixed-price agent exists)

Sokosumi's catalogue (`buildAvailableAgentWhereClause` in masumi-network/sokosumi) lists only agents that are
`status ONLINE`, Standard MIP-003 entries, `isShown` (default on Preprod) and priced **FREE or FIXED** in a billable
unit (ADA/tUSDM); for V2 agents Sokosumi's own node must also report a purchase-ready V2 source. **Dynamic agents are
never listed**, so `67ab0c92…` cannot be hired on Sokosumi. On 2026-10-07 a second SaaS agent was registered:
**"Cardano Card"** `4e0531b8-e453-40d7-ae03-ad52ade78924`, Fixed **20 ADA**, `apiUrl …/v2` (the same deployment via
`CARDANO_CARD_PATH_PREFIXES=/v2`; the SaaS matches registrations to their NFT by exact URL, so the URL had to differ).
Receipt: `work/hosted-registration-fixed-submission.json`. It reached `RegistrationConfirmed` ~20 min after submission
(identifier `67ab0c92…d06e8c000000`). **Railway now serves this agent** (`AGENT_IDENTIFIER`, `MASUMI_LOVELACE_PER_USD=fixed`,
`MASUMI_FEE_LOVELACE=20000000`): every job locks exactly 20 tADA. Each SaaS agent gets its own selling wallet — this one
signs with `79e95441…` (`addr_test1qpu7j4zp…`, funded 10 tADA by Masumi), so `SELLER_VKEY` changed with the agent.
Cardano fixed pricing in the SaaS payload takes `{asset: "", amount: "<lovelace>"}` with no `decimals`.

## Third registration: tUSDM pricing (what Sokosumi actually bills)

Sokosumi's billable units on Preprod are **tUSDM** (`16a55b2a…0014df10745553444d`), not ADA — an ADA-priced Fixed agent
is never "billable" and stays hidden. Registered 2026-10-07 ~14:35 SGT: **CardanoCard**, SaaS agent
`f59af5fc-66e7-47ff-8c7d-569aba1251f1`, URL `…/v3`, Fixed **20 tUSDM**, confirmed within 5 minutes
(identifier `67ab0c92…36cbc1000000`, selling wallet `37d35cc9…`). Railway serves this one now; `/v3/start_job` returns
`RequestedFunds [{unit: <tUSDM>, amount: "20000000"}]`. The ADA (`/v2`) and Dynamic (root) entries remain registered
but unused. SaaS payload: `pricing.prices[{amount:"20", currency:"tUSDM"}]`, source `fixed[{asset:<unit>, amount:"20000000"}]`.

## Who can buy

The hosted SaaS key is **seller-only** (`/pay/api/v1/purchase` and `/wallet` return 404), our local node (0.22) is
V1-only, and V2 purchasing arrived in masumi-payment-service **0.28.0**. A buyer therefore has to be one of:

1. **Sokosumi Preprod** (https://preprod.sokosumi.com) — Masumi's marketplace, hires registry agents with test
   credits. Needs a human login; the agent should appear there once the registry syncs.
2. Any agent/operator running masumi-payment-service ≥ 0.28 with a funded Preprod purchasing wallet, calling
   `/start_job` here and then `POST /purchase` on their node with the returned terms (`paymentSourceType:
   Web3CardanoV2`, `Amounts` = `RequestedFunds`).

Evidence: NOWNodes/Blockfrost inclusion checks work for V2 transactions; the strict payout/refund decoder is V1-only,
so hosted settlements are reported as observed (not strictly verified) until the V2 datum layout is mapped.
