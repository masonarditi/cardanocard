# AgentCard sandbox acceptance

This command runs our actual `Engine` and durable `AgentCardPurchaser` against the AgentCard sandbox HTTP API. The service-fee escrow is explicitly simulated. It prints the colored lifecycle feed directly in the terminal and saves a private evidence bundle. It does **not** prove a Cardano escrow transaction or a merchant purchase.

## Current pause after repository migration

The new workspace restored the saved sandbox database and evidence, but deliberately did not copy rotating AgentCard credentials. The later gum attempt (`81efeedd-26a1-4ab4-88c4-7d9be7beaeb2`) remains `reconciling` / `cart_requested` without a usable conversation ID, with zero confirmations. Have Mason reconcile its provider logs before starting another checkout. Do not use a new request ID or database to bypass this uncertainty. The combined [Preprod acceptance runner](PREPROD_ACCEPTANCE.md) enforces that prior-state guard.

## Prerequisites

1. Mason's matching sandbox credentials in `../agentcard/.env` and linked user tokens in `../agentcard/.agentcard_tokens.json`. Root organization credentials are not substituted.
2. A current exclusive handoff: Mason stops all use, including reads, of the shared token. A local file lock only prevents two of our runners; it cannot lock Mason's machine or his original scripts.
3. A JSON file containing `ask`, `max_total_usd`, and the US address fields from our input schema. This runner supports the single-cart contract.

Run the check without touching any API or refreshing a token:

```sh
.venv/bin/python -m cardano_card.sandbox_run
```

After the handoff, run with one stable caller ID (26 lowercase hexadecimal characters):

```sh
.venv/bin/python -m cardano_card.sandbox_run \
  --execute --exclusive-handoff \
  --request work/sandbox-request.json \
  --request-id 65b2a4f80d13579ace2468bdf0
```

Reuse that request file and ID when resuming. The fixed ID above belongs to this example; choose another only for an intentionally new purchase request. The runner saves jobs in `data/agentcard-sandbox.db` and evidence under `work/sandbox-evidence/`. The regular local API remains a separate simulation and its `live` viewer reads its own database.

The runner first exchanges **only the matching organization credentials**, then checks `GET /api/v2` for `test_mode: true`. It refuses production or unknown modes before user access. The runtime handoff expires after the run timeout (default five minutes, maximum thirty). User-token refresh is checkpointed before the request and atomically saves rotated tokens. An uncertain refresh leaves a marker that blocks another attempt, including after restart. Stop on that condition and arrange fresh files/re-linking with Mason; do not simply delete the marker and retry an old refresh token. No Vault setup is performed.

## Expected result and current limits

The intended sandbox path is job → simulated funding → actual AgentCard cart → one confirmation → `sandbox_mode` with `charge_status: none` → simulated refund. The command reports PASS only when that exact provider reason and refund outcome are saved. It never auto-pays out based on unexpected sandbox order evidence.

The public cart schema does **not** include an explicit currency field. The transport now checks authenticated `GET /buy/merchants` market metadata for a unique matching merchant with USD and US support. It records that source as currency evidence. If that mapping is absent or ambiguous, confirmation remains blocked; USD is never inferred from a missing field. Order tracking also remains deliberately unimplemented for this sandbox-only transport: unexpected order evidence stops for review.

The conversation recovery mapping now accepts documented `orders[].order_id` and reads an explicit no-charge `last_checkout` denial after a lost confirmation. Silence, an in-progress checkout, partial orders, or unknown charges do not become refundable failures.

The live conversation also exposes `carts[]`, including `totalIsEstimate`, `estimatedTotalCents` and `approvedCeilingCents`. Budget checks now consider the largest of the reported total, estimate and authorization ceiling. An estimated total requires a valid ceiling. The bridge saves the candidate cart before validation and may reject a validation-paused cart during read-only recovery, but inspection never initiates confirmation.

## Actual sandbox run — 6 October 2026

With the user's explicit exclusive handoff and approval of the sample contact/address payload, organization introspection returned `test_mode: true`. One user-token refresh succeeded and atomically saved the rotated tokens. The actual `/buy` request returned a single Amazon cart. No explicit currency was supplied. A read of that same idle conversation showed no orders or last checkout, a reported estimate of 1,241 cents and approval ceiling of 2,348 cents against our 2,000-cent cap.

Job `f5db882e-111a-4d3d-87cf-58fd09a80491` resumed using its existing conversation, rejected the higher ceiling, and completed **simulated** refund. Confirmation attempts: **0**. This proves the real AgentCard cart-to-budget-rejection branch through our engine, not the `sandbox_mode` confirm branch or Cardano settlement. Evidence is in ignored `work/sandbox-evidence/f5db882e-111a-4d3d-87cf-58fd09a80491.json`. Mason must receive the updated token file privately before resuming shared-token use.

Tests use mocked HTTP responses, including an explicit-USD fixture. They are not evidence of a real authenticated sandbox run. The complete live target additionally requires funded Preprod wallets, a registered agent, buyer funding, real refund/payout, and independently checked transaction evidence.

Sources inspected on 6 October 2026: [credential introspection](https://docs.agentcard.sh/api-reference/access-tokens/introspect), [Buy](https://docs.agentcard.sh/api-reference/purchases/buy), [conversation status](https://docs.agentcard.sh/api-reference/purchases/conversation), [sandbox behavior](https://docs.agentcard.sh/vault/integrations/ecommerce-apis/purchase-api).
