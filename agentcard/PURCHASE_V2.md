# Purchase interface v2 (`agentcard/purchase_v2.py`)

Internal methods for Ezra's coordinator (not Agentcard endpoints). v1 `purchase()` is unchanged.

```python
import purchase_v2 as agentcard   # with <repo>/agentcard on sys.path
agentcard.prepare_purchase(job_id: str, intent: str, max_total_usd, address: dict) -> dict  # never confirms
agentcard.confirm_purchase(job_id: str, quote_id: str) -> dict  # only after escrow is funded + quote approved
agentcard.inspect_purchase(job_id: str) -> dict                 # read-only
agentcard.whoami() -> dict                                       # env + Agentcard user, offline
```

- Every outcome is `{"version": 2, "status", "job_id", "conversation_id", ...}`. Money is in integer cents.
- Provider errors never raise. `ValueError` only for caller errors: a `job_id` reused with different inputs, or an
  unknown `job_id`.
- Each job is saved to `agentcard/.purchase_jobs.json` (`.purchase_jobs.prod.json` with `AGENTCARD_ENV=prod`) before
  any Agentcard call. Each confirm attempt is saved before it is sent. A confirm that may have charged is only
  ever inspected, never re-sent. Repeating `prepare_purchase` or `confirm_purchase` for a job returns its state.
- Payment is always `payment_source: "vault"` (the already-linked card). Budget = `min(max_total_usd, $50)`.

## Outcomes

| status | meaning | coordinator does |
|---|---|---|
| `prepared` | Quote saved. Nothing confirmed. | Customer approves `quote`, escrow is funded, then `confirm_purchase(job_id, quote_id)` |
| `approval_required` | `reason: vault_approval_required` (+ `approval_url`): card approval needed. `cart_changed` / `quote_changed`: new `quote`. | Vault: after approval, `confirm_purchase` again with the same `quote_id` (or `inspect_purchase`). Changed: get fresh approval, confirm the new `quote_id` |
| `needs_input` | `/buy` asked a question (`message`). Nothing confirmed. | New job with a clearer intent |
| `pending` | `reason`: `in_progress`, `charge_confirming`, `order_not_in_ledger_yet`, `unresolved`, `inspect_failed`, `order_mismatch`, `order_cancelled` | Keep calling `inspect_purchase`. Never refund |
| `partial` | More than one order, or `partially_placed`. | Manual reconciliation. Not success, not refundable |
| `confirmed` | The saved order is in Agentcard's order ledger with `status: settled` (placed and charged). `order.total_cents` is the all-in charge. | Submit result to Masumi |
| `failed_no_purchase` | `reason`: `over_budget`, `ambiguous_amount`, `unsupported_currency`, `unexpected_items`, `multiple_carts`, `no_cart`, `declined` (+ `decline_code`), `funding_failed`, `error`, `prepare_interrupted` | Safe to refund |

`confirmed` evidence: `GET /buy/conversations/{id}` → `orders[]` entry for the saved `order_id` with `status:
settled`. Amazon's own order number only arrives in the `order.confirmed` webhook, which needs a public endpoint
(not wired; can be added once the tunnel exists). An order ID on its own is reported as `pending`.

## Spending ceiling and currency

The quote returns `subtotal_cents` (`cart.totalCents`: items + Agentcard service fees), `estimated_total_cents`,
`ceiling_cents` (`approvedCeilingCents`) and `currency`. Confirmation needs `max(all present amounts) ≤ budget`.
A non-integer or negative amount, or `totalIsEstimate: true` with no ceiling, is `ambiguous_amount`. Carts carry no
currency field; Agentcard sets `merchant_currency` only for non-USD carts, so null means USD. Anything else is
`unsupported_currency`.

The ceiling is ~$11 above the subtotal, so the approved budget must be at least that: sandbox 12-pack gum is
$12.34 with a $23.41 ceiling; a production single pack was $1.32 with a ~$12.06 ceiling (actual charge $2.70).

## Agentcard behaviour found while testing

- One Amazon cart per user that persists across conversations: a declined job's items showed up in the next
  quote. `prepare_purchase` asks `/buy` to empty the cart first, and blocks (`unexpected_items`) any cart item that
  isn't in this turn's search results (`catalog`). A changed cart on confirm (409) must keep the approved items.
- A declined confirm can come back with `status: "needs_input"`. Only `decline_code` and `charge_status` are
  reliable (`charge_status: "none"` = nothing charged).

## Response examples (live sandbox, 2026-10-06; names shortened)

```json
{"version": 2, "status": "prepared", "job_id": "sbx-gum-2", "conversation_id": "conv_3a93ed6a30215f537c20c231",
 "quote": {"quote_id": "q_ad97323ea23dbc50", "cart_hash": "70645fb5dc65b52b", "merchant": "Amazon",
   "items": [{"name": "Trident Original Sugar Free Gum, 12 Packs…", "qty": 1, "price_cents": 1204,
              "product_id": "https://www.amazon.com/dp/B0711V757H"}],
   "currency": "usd", "subtotal_cents": 1234, "estimated_total_cents": 1234, "ceiling_cents": 2341,
   "total_is_estimate": true, "budget_cents": 5000}}

{"version": 2, "status": "failed_no_purchase", "job_id": "sbx-gum-2", "conversation_id": "conv_3a93ed6a30215f537c20c231",
 "reason": "declined", "decline_code": "sandbox_mode"}

{"version": 2, "status": "failed_no_purchase", "job_id": "sbx-gum-1", "conversation_id": "conv_8f17558a7f32b79726cdebc1",
 "reason": "over_budget", "quote": {"items": ["<coffee left from an earlier job>", "<gum>"], "subtotal_cents": 2475,
 "ceiling_cents": 3618, "budget_cents": 1500, "...": "..."}}
```

Shapes from the offline tests (no v2 production run yet):

```json
{"version": 2, "status": "approval_required", "job_id": "j", "conversation_id": "conv_1",
 "reason": "vault_approval_required", "approval_url": "https://vault.agentcard.sh/authorize?id=cauth_…", "quote": {"...": "..."}}

{"version": 2, "status": "pending", "job_id": "j", "conversation_id": "conv_1", "reason": "charge_confirming",
 "order": {"order_id": "ord_1", "status": "confirming", "total_cents": 270, "merchant_name": "Amazon", "placed_at": "…"}}

{"version": 2, "status": "confirmed", "job_id": "j", "conversation_id": "conv_1",
 "order": {"order_id": "ord_1", "status": "settled", "total_cents": 270, "merchant_name": "Amazon", "placed_at": "…"},
 "quote": {"...": "..."}}
```

## Readiness

- Sandbox (user `usr_31f5c35616a38795b45ea5ec`): live prepare → confirm → `sandbox_mode` → `failed_no_purchase`.
- Production (user `usr_f9fe7033a2dc8fe3896baeb1`, real card, auto-approval): token verified read-only 2026-10-06.
  No v2 production purchase yet; that's the coordinated real-card test.
- Tokens live only on Mason's machine. Refresh tokens rotate, so only one machine may use them.
- The earlier sandbox gum request belonged to the old sandbox user, whose token is dead. Sandbox can't place
  orders, and Ezra's notes record 0 confirms, so there is nothing to reconcile.

## Tests

`cd agentcard && .venv/bin/pip install pytest && .venv/bin/python -m pytest tests` → 23 passed. They cover preparing
without spending, confirmed only once settled, vault approval (re-confirm, and Agentcard placing it on approval),
over-budget, ambiguous amount, non-USD and leftover-item carts, changed cart with fresh approval, timeout, duplicate
requests, restart mid-prepare and mid-confirm, partial orders, sandbox declines, and 5xx as pending.

## Integration

```python
q = agentcard.prepare_purchase(job_id, intent, max_total_usd, address)  # ~30-60s
# status prepared → customer approves q["quote"]; escrow funded
r = agentcard.confirm_purchase(job_id, q["quote"]["quote_id"])          # ~45s
while r["status"] == "pending":
    time.sleep(10); r = agentcard.inspect_purchase(job_id)
```

Calls block, so run them in a thread (`asyncio.to_thread`). Use one process: the ledger is a local JSON file with
no cross-process lock, and it must stay on the machine that ran the job. Keep the gap between prepare and confirm
short, because a stale cart comes back as `approval_required` / `cart_changed` or a `no_cart` decline.
