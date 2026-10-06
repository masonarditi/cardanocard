# Purchase interface v2 (`agentcard/purchase_v2.py`)

Implements the "Mason v2 handoff" in `masumi/docs/INTEGRATION_PLAN.md`. Responses validate against masumi's
`staged_purchase.PreparedQuote` / `CheckoutOutcome`. v1 `purchase()` is unchanged.

```python
PURCHASE_PROTOCOL_VERSION = 2
prepare_purchase(*, job_id, intent, max_total_usd, address) -> dict  # never confirms
confirm_purchase(*, job_id, quote_id) -> dict  # only after escrow is funded and the quote approved
inspect_purchase(*, job_id) -> dict            # read-only
whoami() -> dict                               # env + Agentcard user, offline
```

Run it in Ezra's app with `PURCHASE_BACKEND=staged_module MASON_STAGED_MODULE=purchase_v2`, with `<repo>/agentcard`
on `PYTHONPATH` (plus his opt-in flags). `AGENTCARD_ENV=prod` selects production credentials and ledger.

## Guarantees

- Each job is saved to `agentcard/.purchase_jobs.json` (`.purchase_jobs.prod.json` in prod) before any Agentcard
  call. Each confirm attempt is saved before it is sent. A confirm that may have charged is only ever inspected,
  never re-sent. Repeating `prepare_purchase` returns the saved state; reusing a `job_id` with different inputs, an
  unknown `job_id`, or a `quote_id` that isn't the job's saved quote raises `ValueError`.
- Always `payment_source: "vault"` (the already-linked card). Budget = `min(max_total_usd, $50)`; confirmation
  needs `max(subtotal, estimate, approvedCeilingCents) ≤ budget`.
- Responses carry only schema fields: no address, card data or Agentcard reply text.

## Responses

**Before checkout is requested** (`prepare_purchase`, and `inspect_purchase` until `confirm_purchase` is called):
a `PreparedQuote`. Over-budget carts are still quoted so the coordinator rejects them on the ceiling. Any other
problem returns a `CheckoutOutcome` `failed_no_purchase` (`unexpected_items`, `ambiguous_amount`,
`unsupported_currency`, `multiple_carts`, `no_cart`, `error`, `prepare_interrupted`) or `needs_input`.

- `currency`: carts have no currency field; Agentcard sets `merchant_currency` only for non-USD carts, so null
  means USD.
- `subtotal_cents` = `cart.totalCents` (items + Agentcard service fee). `estimated_total_cents`,
  `authorization_ceiling_cents` = `estimatedTotalCents`, `approvedCeilingCents` (each falls back to the total when
  Agentcard omits it on a non-estimate cart).
- `expires_at` = quote time + 30 min. Agentcard doesn't expose a cart expiry; a stale cart surfaces at confirm.

**After `confirm_purchase`**: a `CheckoutOutcome`.

| status | when | fields |
|---|---|---|
| `confirmed` | The saved order is in Agentcard's order ledger (`GET /buy/conversations/{id}`) with `status: settled` | `merchant_confirmed: true`, `charge_status: captured`, `order_id`, `total_cents` (all-in charge), `merchant`, `items` |
| `failed_no_purchase` | Agentcard said nothing was charged (`charge_status: none`), or checkout was refused before sending | `no_purchase: true`, `charge_status: none`, `reason`: `sandbox_mode` or another decline code, `over_budget`, `cart_changed` (409: new price/cart, no charge), `funding_failed`, `error` |
| `approval_required` | Vault approval needed | `approval_url`; after approval Agentcard places the order itself, so call `inspect_purchase` |
| `pending` | `in_progress`, `charge_confirming`, `order_not_in_ledger_yet`, `unresolved`, `inspect_failed`, `order_mismatch`, `order_cancelled` | `order_id` when known; keep inspecting, never refund |
| `partial` | `partially_placed` or several orders | Manual reconciliation |

`merchant_confirmed` means "settled in Agentcard's ledger". Amazon's own order number only comes with the
`order.confirmed` webhook, which needs a public endpoint (not wired).

## Agentcard behaviour found while testing

- One Amazon cart per user, persisting across conversations: a declined job's items showed up in the next quote.
  `prepare_purchase` asks `/buy` to empty the cart first and fails `unexpected_items` if any cart item isn't in this
  turn's search results (`catalog`).
- A declined confirm can come back with `status: "needs_input"`; only `decline_code` and `charge_status` are reliable.
- The ceiling is ~$11 above the subtotal, so the budget must cover it: sandbox 12-pack gum $12.34 / ceiling $23.41;
  production single pack $1.32 / ceiling ~$12.06 (actual charge $2.70).

## Examples (live sandbox through Ezra's StagedEngine, 2026-10-06)

```json
{"status": "prepared", "job_id": "4a55701e-c3a2-41e7-bdc5-1d54aa6565b1", "quote_id": "q_fdef4ded4d06e31c",
 "conversation_id": "conv_…", "cart_hash": "…", "currency": "USD", "payment_source": "vault",
 "subtotal_cents": 1234, "estimated_total_cents": 1234, "authorization_ceiling_cents": 2341,
 "expires_at": 1791262000, "merchant": "Amazon",
 "items": [{"name": "Trident Original Sugar Free Gum, 12 Packs of 14 Pieces (168 Total Pieces)", "quantity": 1}]}

{"status": "failed_no_purchase", "job_id": "4a55701e-…", "quote_id": "q_fdef4ded4d06e31c", "conversation_id": "conv_…",
 "cart_hash": "…", "currency": "USD", "payment_source": "vault", "order_id": null, "reason": "sandbox_mode",
 "no_purchase": true, "charge_status": "none"}
```

Confirmed shape (offline tests; no v2 production run yet):

```json
{"status": "confirmed", "job_id": "…", "quote_id": "…", "conversation_id": "conv_…", "cart_hash": "…",
 "currency": "USD", "payment_source": "vault", "order_id": "ord_…", "reason": null, "merchant_confirmed": true,
 "charge_status": "captured", "total_cents": 270, "merchant": "Amazon", "items": [{"name": "…", "quantity": 1}]}
```

## Tests

- `agentcard/.venv/bin/python -m pytest agentcard/tests`: 22 unit tests (Agentcard API faked). They cover preparing
  without spending, confirmed only once settled, vault approval, over-budget, ambiguous, non-USD and leftover-item
  carts, changed cart, wrong quote, timeout, duplicates, restart mid-prepare and mid-confirm, partial, sandbox
  declines, and 5xx as pending.
- Run with masumi's venv, `test_staged_contract.py` drives Ezra's real `StagedEngine` + `StagedModule` with this
  module (5 tests): success → payout, `sandbox_mode` → refund, charge confirming → payout, partial never refunded,
  over-budget rejected before escrow. It's skipped in the plain agentcard venv.
- Live: Ezra's StagedEngine + this module + Agentcard sandbox + simulated escrow → quote → approve → fund →
  `sandbox_mode` → refunded (52s).

## Readiness

- Sandbox user `usr_31f5c35616a38795b45ea5ec`: works end to end (above).
- Production user `usr_f9fe7033a2dc8fe3896baeb1` (real card, auto-approval): token verified read-only; no v2
  production purchase yet.
- Tokens live only on Mason's machine; refresh tokens rotate, so only one machine may use them.
- Old gum job `81efeedd-…`: it sent zero confirms, and per Agentcard's `/buy` reference "money only moves on a
  confirm", so no purchase happened and it can be closed.
