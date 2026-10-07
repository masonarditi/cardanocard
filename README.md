# Cardano Card

**A credit card for every agent on Cardano.**

Agents on [Masumi](https://www.masumi.network) can already hire and pay each other on Cardano. What they cannot do is
buy anything in the real world: Amazon and almost every other merchant only take a card at checkout. Cardano Card is a
Masumi agent that closes that gap. Any other agent locks a payment in Masumi escrow, Cardano Card buys the item with a
real card through [Agentcard](https://agentcard.sh), and the escrow pays out only once the order is placed. If the
purchase does not happen, the buyer gets their money back from the escrow — automatically, on-chain.

> Built in 48 hours at **TOKEN2049 Origins, Singapore (October 2026)** by Mason and Ezra.

## What it does

```
buyer agent ──MIP-003──▶ Cardano Card ──Purchase API──▶ Agentcard ──card──▶ Amazon
      │                        │
      └── locks 20 tUSDM in ───┘  order placed  → escrow pays Cardano Card
          Masumi escrow           no purchase   → escrow refunds the buyer
```

1. **Request.** A buyer agent calls `start_job` on Cardano Card's MIP-003 API with what it wants, a USD budget and a
   US delivery address, and receives Masumi escrow terms.
2. **Escrow.** The buyer locks the fee (20 tUSDM on Preprod) in the Masumi smart contract on Cardano.
3. **Purchase.** Cardano Card sees the funds on-chain, asks Agentcard to build the Amazon cart, checks it against the
   budget, and confirms checkout with the vaulted card.
4. **Proof.** The order id goes on-chain as the job result and the escrow releases the payment to Cardano Card. A
   definite no-purchase (over budget, declined, nothing found) instead refunds the buyer from escrow.

Nothing is ever bought twice: every purchase is keyed by the job id and saved before it is sent, and an uncertain
outcome is inspected rather than retried.

## Proven on Cardano Preprod with a real card

Both outcomes were exercised end-to-end on 7 October 2026 against the agent listed on
[Sokosumi Preprod](https://preprod.sokosumi.com) (agent `CardanoCard`, identifier
`67ab0c92…36cbc1000000`, escrow contract `addr_test1wzs4e6…`), with a real card behind Agentcard. Every transaction
was verified independently through a Cardano Preprod node.

| | Payout path (job `991d47ac…`) | Refund path (job `c88ceef6…`) |
|---|---|---|
| Escrow funded | [`76c50351…14e39e1`](https://preprod.cardanoscan.io/transaction/76c50351d72aab7ca731809e9ec02b9443e7c363c995c9dba95ba584014e39e1) (block 5263951) | [`a5cb62ef…c332575`](https://preprod.cardanoscan.io/transaction/a5cb62efbaaecf88ac5ab15597444c30dcc12331e63f4521ad008c157c332575) (block 5263930) |
| Purchase | Amazon order `8fd31445…` — Trident gum, 14-pack, $1.32 on the card | Cart was $14.39 against a $10 budget → **no charge** |
| Result on-chain | [`238d65b6…708475f`](https://preprod.cardanoscan.io/transaction/238d65b6de77412c8e1f7def451b2994df191c59c40593dfc8de5f6f7708475f) (block 5263957) | refund requested [`3e4a9461…`](https://preprod.cardanoscan.io/transaction/3e4a9461cfc8f1803dbe16d4011a1178a29b45306a42a0d9e071a7d54d83f0f7), authorized [`8f904f05…`](https://preprod.cardanoscan.io/transaction/8f904f05c9e26b92f1d92aaa93ec3d9362e50ea6c83d4c1cde70772f16de51a5) |
| Settlement | **paid**: 20 tUSDM to Cardano Card's payout address [`e4aa90d0…4a24f2`](https://preprod.cardanoscan.io/transaction/e4aa90d0f52dafad17db4a2dd1c3b8668d80f703dc69a99aaaac1b2d4c4a24f2) (block 5264294) | 20 tUSDM back in the buyer's wallet: [`a35888c0…ecd8023`](https://preprod.cardanoscan.io/transaction/a35888c0fffeecbeb98477c13538df97118d3fa08ec2f6c9b7bdfdf5cecd8023) |

Escrow lock to on-chain proof of a real order: about five minutes, most of it block time.

More real orders the same evening, each a separate hire of the listed agent and each paid out on-chain to the same
payout address (`addr_test1qpgq4gf9fmzg3gkdnp9jdtujtscr2qdms07tmtp8aqz5ykc7uvlelkeuqsgctx9n4en6xmkf4qhqkumzcc3qwt4845hqsxwadj`,
holding **80 tUSDM** of agent revenue at the time of writing):

| order | item | result on-chain | payout (20 tUSDM) |
|---|---|---|---|
| `f87cd230…` | Trident Vibes, $3.99 | fund [`0d4a5cba…3cfeb`](https://preprod.cardanoscan.io/transaction/0d4a5cbade0cb5ca00ec261ac4207d5fe9797a7a40b0821e091dbd622953cfeb) → result [`a02810e8…679c`](https://preprod.cardanoscan.io/transaction/a02810e8a7119d7b2da654a4183717fcc0d73882525f6438c28f40c81556679c) | [`49355a9c…88e3`](https://preprod.cardanoscan.io/transaction/49355a9c35b7eaba1e9c2ba6e440b730027e623f39bce81e820c2e8dc3af88e3) (block 5264466) |
| `8130ce63…` | Trident 14-pack, $1.32 | fund `0d4a5cba…` (same batch) → result [`d1c62d7f…711b`](https://preprod.cardanoscan.io/transaction/d1c62d7fb9ee760d814c58259ce09f2ad23910048f8150397be93f73f9c1711b) | same batch tx [`49355a9c…88e3`](https://preprod.cardanoscan.io/transaction/49355a9c35b7eaba1e9c2ba6e440b730027e623f39bce81e820c2e8dc3af88e3) |
| `3f766e8d…` | $3.88 item, bought over iMessage | fund [`a377cfaf…6296`](https://preprod.cardanoscan.io/transaction/a377cfafd38b40454aed78a83f83b6d1dfef66c894e764c52e5153d04c166296) → result [`d9974558…4683`](https://preprod.cardanoscan.io/transaction/d99745586bc4c313048c0f7abc05dcfbf9c0feafca2a2b7ca1d60c06adbf4683) | after its dispute window |
| `ed663bf2…` | Life Savers, $4.97 — bought by texting the agent over iMessage | fund [`3fcd4a88…02c4`](https://preprod.cardanoscan.io/transaction/3fcd4a88dc5d1a67d38241c91810b202f3da8412e6798e0b976b0cc6bb1502c4) → result [`45b2e734…9ab3`](https://preprod.cardanoscan.io/transaction/45b2e734ecfb2f759fc0f9f2125699a6a33f4c08131622ca7a7bad39a2669ab3) | after its dispute window |

Every hash was verified independently through a Cardano Preprod node (NOWNodes) before being listed here.

## Try it

Cardano Card is live on Preprod at `https://cardanocard-preprod-production.up.railway.app/v3` and speaks plain
[MIP-003](https://docs.masumi.network):

```bash
curl https://cardanocard-preprod-production.up.railway.app/v3/availability
curl https://cardanocard-preprod-production.up.railway.app/v3/input_schema
```

Hire it from Sokosumi, or from any Masumi payment service (≥ 0.28) with a funded Preprod purchasing wallet: call
`start_job`, pay the returned terms with `POST /purchase` (`paymentSourceType: Web3CardanoV2`), then poll `status`
until the phase is `paid` (order placed, result on-chain) or `refunded`. `masumi/scripts/v2_hire.py` is a complete
reference buyer that does exactly this.

Mason's iMessage / WhatsApp / Telegram front door (`chat/`) lets a human do the same by texting "buy me a pack of
Trident gum under $10" and approving the escrow with Face ID.

## How it is built

| Layer | What | Where |
|---|---|---|
| Agent | FastAPI service implementing MIP-003, a state machine that checkpoints before every side effect, and adapters for Masumi's hosted payment service (Web3CardanoV2) and self-hosted nodes (V1) | `masumi/` |
| Purchasing | Agentcard Purchase API client: `purchase()` (one-shot) and a staged `prepare / confirm / inspect` interface with a USD price ceiling, idempotent confirms and a local ledger | `agentcard/` |
| Chat front door | Photon Spectrum bot + buyer agent: text a request, approve with a passkey, follow the order | `chat/` |
| Buyer reference | A Web3CardanoV2-capable Masumi node and a script that hires the live agent and pays its escrow | `masumi/infra/masumi-v2buyer/`, `masumi/scripts/v2_hire.py` |
| Operator page | `/ops` on the runtime: live jobs, events, every rail's health (Agentcard, Masumi escrow service, Sokosumi listing, own node) and a kill switch that takes the real card offline instantly | `masumi/src/cardano_card/ops.py` |
| Chain evidence | Independent verification of every funding, result and refund transaction through a Preprod node | `masumi/src/cardano_card/chain_evidence.py` |
| Deck & site | Pitch deck, demo video source and the landing page | `demo/`, `website/` |

Safety rules the engine enforces: the card is charged only after the buyer's funds are confirmed locked on Cardano;
Agentcard's authorization ceiling must fit the approved budget; a refund is issued only on a definite no-purchase,
never while an outcome is uncertain; and a crash or retry can never place a second order for the same job.

## Status and what is next

- Live on Cardano Preprod and listed on Sokosumi; payout and refund both proven with a real card (above).
- Next: per-caller spending limits before strangers can spend on a real card, mainnet (ADA / USDM escrow), and
  crypto-funded single-use cards issued per purchase instead of one vaulted card.

Engineering notes for contributors live in `CLAUDE.md` and `masumi/docs/`.
