# Cardano Card — submission notes (TOKEN2049 Origins, 7 Oct 2026)

**A credit card for every agent on Cardano.** Agents on Masumi can pay each other, but nothing in the real world takes
ADA or USDM at checkout. Cardano Card is a Masumi agent that closes that gap: a buyer locks 20 tUSDM in Masumi escrow,
Cardano Card buys the item on Amazon with a real card through Agentcard, commits the order on-chain, and the escrow pays
out. If nothing gets bought, the buyer is refunded from escrow — automatically, on-chain.

## What we built (all live on Cardano Preprod)

- **Masumi agent `CardanoCard`**, registered and listed on Sokosumi Preprod, speaking plain MIP-003
  (`https://cardanocard-preprod-production.up.railway.app/v3`). Fixed price 20 tUSDM, Web3CardanoV2 escrow.
- **Real purchasing** through Agentcard's Purchase API with a vaulted credit card (production, not sandbox).
- **A human front door**: text the agent over iMessage / WhatsApp / Telegram ("buy me Orbit gum under $20"),
  approve the escrow with Face ID, get the order confirmation back in the thread.
- **Operator console** (`/ops`): live jobs and events, health of every rail (Agentcard, Masumi payment service,
  Sokosumi listing, our node), a kill switch that takes the real card offline instantly, and a resolve action for
  the rare case a card provider never answers.
- **Independent on-chain verification** of every lock, result, payout and refund via NOWNodes (Cardano Preprod,
  Blockfrost-compatible API), with a genesis `network_magic` check so only Preprod evidence can pass.

## Proof — real orders, settled on-chain (today)

| | on-chain |
|---|---|
| Amazon order `8fd31445…` (Trident, $1.32) | fund `76c50351…` → result `238d65b6…` → **payout** `e4aa90d0…` |
| Amazon order `f87cd230…` (Trident Vibes, $3.99) | result on-chain → **payout** `49355a9c…` (block 5264466) |
| Amazon order `8130ce63…` (Trident, $1.32) | result on-chain → **payout** `49355a9c…` (same batch) |
| Amazon order `ed663bf2…` (Life Savers, $4.97), bought from iMessage | fund + result on-chain, payout after its dispute window |

**Agent revenue actually received:** 80 tUSDM at the payout address `addr_test1qpgq4gf9…sxwadj` (four 20 tUSDM payouts).
| Refund path (cart over budget, nothing charged) | fund `a5cb62ef…` → request `3e4a9461…` → authorize `8f904f05…` → **20 tUSDM back to buyer** `a35888c0…` |

Escrow contract: `addr_test1wzs4e6wc95hkwezlccjw9mdvq0r0rsgx6zk34avptga3ftgn37w4g` (Masumi V2, Preprod). Every hash
above is viewable on Cardanoscan Preprod and was re-verified independently through NOWNodes.

## How the money is protected

- The card is charged only after the buyer's funds are confirmed locked on Cardano.
- Nothing is ever bought twice: every purchase is keyed by the job id and checkpointed before it is sent; a crash or
  retry can only inspect, never re-order. Identical carts are refused (Agentcard treats them as the same order).
- A refund is issued only on a definite no-purchase (over budget, declined, nothing found). An uncertain outcome is
  held for a human — never auto-refunded against a possible charge.
- The order id goes on-chain as the escrow result hash, so the payout is tied to a verifiable purchase.

## Stack

Masumi (hosted payment service, Web3CardanoV2 escrow, Sokosumi) · Cardano Preprod · Agentcard Purchase API ·
NOWNodes (chain verification) · Photon Spectrum (iMessage/WhatsApp/Telegram) · Railway (agent runtime, buyer node,
chat service). Python/FastAPI engine with a checkpointed state machine; Bun/TypeScript bot; 450+ tests.

## Where this goes

Per-caller spending limits so strangers can spend on a real card; mainnet with ADA/USDM; Agentcard-issued single-use
cards funded per purchase (x402) instead of one vaulted card.

Repo: README has the full transaction table with explorer links; `masumi/docs/HOSTED_RUNTIME.md` has the deployment
and evidence details.
