# Cardano Card

Hackathon project (TOKEN2049 Origins, Singapore). **Deadline: 11:59pm SGT, 7 Oct 2026.**

**What it is:** a Masumi agent (Cardano) that lets any other Masumi agent buy real things with a credit card.
Buyer agent locks funds in Masumi escrow → our agent buys via Agentcard's Purchase API → order placed = escrow
pays us; purchase fails = buyer is refunded. Pitch: "Any agent on Cardano can buy from Amazon, with a refund if
the purchase fails."

## Repo layout
- `agentcard/` — Agentcard side (owner: Mason). Exposes `purchase()`.
- `masumi/` — Masumi agent (owner: Ezra). Calls `purchase()` through `ModulePurchaser` (`PURCHASE_BACKEND=mason`).

## The contract (do not change without telling both people)

```python
from purchase import purchase
purchase(ask: str, max_total_usd: float, address: dict, request_id: str = None) -> dict
inspect_purchase(request_id: str) -> dict   # read-only; never starts or confirms a checkout
# success: {"status": "success", "order_id", "total_usd", "merchant", "items"}
# failure: {"status": "failed", "reason", ...}   (no charge happened)
#   reasons: no_cart, over_budget, needs_input, declined, approval_required,
#            sandbox_mode, price_changed, error
# pending: {"status": "pending", "reason": "unknown", "conversation_id", "detail"}
#   (confirm sent but outcome unknown; call inspect_purchase later, never refund yet)
# address: {"street","city","state","zip","phone","name"} (+address2). US/Canada only.
```
Any `failed` → Masumi side refunds the buyer's escrow. `purchase()` never raises. With a `request_id`, each
request is saved to `agentcard/.purchases.json`; repeating it returns the saved result instead of buying again.

## Agentcard side (`agentcard/`)
- `agentcard.py` — platform token (client credentials, cached 1h, rate limit 30/5min), user token with
  auto-refresh (each refresh returns a NEW refresh token; always save it), `/buy` helpers.
- `setup_vault.py` — one-time: create vault session → user opens link, adds card with passkey → poll until
  `linked` → exchange session (works ONCE) → save `.agentcard_tokens.json`. `--partner` = request app
  auto-approval (production only). `--connect EMAIL_OR_PHONE` = one-time code instead of a vault link (also used
  automatically if the exchange fails with `account_verification_required`).
- `purchase.py` — the contract. `/buy` with ask + delivery_address → check `cart.totalCents` ≤ cap → confirm
  `cart.hash` with `payment_source: "vault"`. Never resend a timed-out confirm; read the conversation instead.
- `test_purchase.py` — sandbox smoke test. Expected: case 1 `sandbox_mode`, case 2 `over_budget`.
- `purchase_v2.py` — v2 interface for Ezra's coordinator: `prepare_purchase` / `confirm_purchase` / `inspect_purchase`.
  Spec, outcomes, real response shapes and integration: `agentcard/PURCHASE_V2.md`. Offline tests: `agentcard/tests/`.

Base URL `https://api.agentcard.sh` (sandbox vs production is set by which credentials are used).
Docs index: https://docs.agentcard.sh/llms.txt — Purchase API: https://docs.agentcard.sh/vault/integrations/ecommerce-apis/purchase-api.md
Auto-approval: https://docs.agentcard.sh/vault/app-auto-approval.md

### Status
- [x] Code written, compiles; sandbox credentials verified (token exchange works). Code was missing from the
      repo on 2026-10-06 and was rebuilt from the docs. Setup: `cd agentcard && python3 -m venv .venv &&
      .venv/bin/pip install -r requirements.txt`; `.env` holds AGENTCARD_CLIENT_ID / AGENTCARD_CLIENT_SECRET
- [x] Sandbox user is now `usr_31f5c35616a38795b45ea5ec` (re-linked 2026-10-06 via `--connect` with Mason's phone,
      code 111111, after the shared refresh token was used up). No vault card needed: confirm still returns `sandbox_mode`
- [x] `test_purchase.py` passes in sandbox (case 1 `sandbox_mode` on a $12.41 cart, case 2 `over_budget`; ~45s/case)
- [x] Production credentials obtained, no approval needed (token exchange returns `mode: production`).
      Run anything with `AGENTCARD_ENV=prod` → uses `.env.prod` + `.agentcard_tokens.prod.json`
- [x] Dashboard: Live → Settings → Vault → Customize → Card enrollment → app → Request app auto-approval (limit $10)
- [x] `AGENTCARD_ENV=prod setup_vault.py --partner` with existing real card (prod user `usr_f9fe7033a2dc8fe3896baeb1`;
      prod user token verified with GET /buy/merchants)
- [x] ONE cheap real purchase to a US address: Trident gum $1.32, order `0e3eda05-66a2-4e24-bfcd-79aea94196a7`
      (2026-10-06). Auto-approval covered `/buy` with no approval link, even with a $12.06 ceiling > $10 limit.
      The confirm response didn't say `order_placed`, so `purchase()` wrongly returned `declined`; fixed to
      treat any `order_id` as success and check `last_checkout` on any non-explicit decline.
      → `success` means auto-approval covers `/buy`; `approval_required` means it doesn't
- [ ] Approval fallback (send `approval_url` to phone, re-confirm same hash): probably unneeded while purchases stay < $10
- [ ] Untested in a real run: confirm timeout, `in_progress`, 409 price change. Decided: no offline tests for these.
- [x] Integrated with Ezra's agent: `PURCHASE_BACKEND=mason` (masumi `ModulePurchaser`) loads `agentcard/purchase.py`.
      Sandbox + fake escrow run passed 2026-10-06: job funded → real $12.41 cart → `sandbox_mode` → refunded (~50s)

### Open decisions
- Keep `CARD_LIMIT_USD` at $50: auto-approval covered a $12.06 ceiling despite its $10 limit, and every cart's ceiling
  is ~subtotal + $11, so a $10 cap would block everything. Keep demo purchases cheap (< $10 subtotal).
- Budget check compares item subtotal only → recommend subtotal + $1.50 for fees. Checking `approvedCeilingCents`
  (~subtotal + $11) would block almost every cheap item.
- Optional: register a webhook so `order.placed` events show in the dashboard for the demo.

### Known gotchas
- Sandbox can never complete a purchase: confirm returns `declined` / `sandbox_mode` by design.
- App auto-approval only works with production credentials.
- Purchase API merchants are mostly US; delivery address must be US/Canada; retail needs `phone`.
- If the vault link is opened by someone already signed into Agentcard, the exchange fails with
  `account_verification_required` → use /api/v2/connect/start + /verify, or a fresh session.
- Print/send vault URLs untouched (any extra char breaks the signed token).
- A vague ask makes `/buy` list options and ask which one (`needs_input`, no cart). `purchase()` appends
  `NO_QUESTIONS` to the ask so it picks the best-value match itself.
- Retail `cart.totalCents` is item subtotal only (`totalIsEstimate: true`). Real charge = subtotal + Amazon shipping
  (none under $35 unless Prime) + CA tax + $1.00 order fee + 2.9%+$0.30, settled after placement. Charge is capped
  at `approvedCeilingCents` (~subtotal + $10.74 for a $1.32 gum cart). Actual gum order: retailer total $1.32
  (free shipping, no tax), card charged $2.70 (Agentcard fee + processing = $1.38). REST
  `/buy/v1/merchants/retail/orders/{id}/track` is retired (410); ask `/buy` on the same conversation_id instead.
- Vault purchases don't appear on the dashboard's Transactions page (it only lists org-issued cards).
- Agentcard keeps ONE Amazon cart per user across conversations: a declined job's items reappear in the next cart.
  v2 asks `/buy` to empty the cart first and blocks items not in the turn's `catalog` (`unexpected_items`).
- A declined confirm can have `status: "needs_input"`; trust only `decline_code` + `charge_status` (`none` = no charge).
- Card budget is $50: `purchase()` caps every cart at `min(max_total_usd, CARD_LIMIT_USD=50)`. Set the
  Masumi price tier to match.

## Masumi side (`masumi/`) — handoff for Ezra's agent
Agentcard side is done and wired into Ezra's engine (commits `2b0039a`, `6e9691d`). Changes to `masumi/` were additive:
`ModulePurchaser.normalize` in `providers.py`, the `mason` backend in `api.py`, `tests/test_module_purchaser.py`.
Ezra's fake and replay backends are unchanged. 115 tests pass.

### Running with the real purchaser
- `cd masumi && pip install -e '.[agentcard,test]'` (`requests` is needed by `agentcard/`).
- `CARDANO_CARD_MODE=local PURCHASE_BACKEND=mason CARDANO_CARD_TOKEN=<24+ chars> cardano-card` → sandbox Agentcard.
  Add `AGENTCARD_ENV=prod` for real purchases on Mason's card. Fake escrow: `simulated_escrow=true, simulated_purchase=false`.
- `buyer.py`, `terminal demo` and `/local/start_job` refuse a non-simulated purchaser. Drive it over HTTP instead:
  `POST /start_job` → `POST /local/jobs/{id}/fund` → poll `GET /status` → `request_refund` on `refund_due` /
  `withdraw` on `result_submitted`.
- Proven 2026-10-06: job `0c803187…` funded → real $12.41 cart → `sandbox_mode` → `refund_due` → `refunded` in ~50s.

### How results map (`ModulePurchaser.normalize`)
- `success` → Success with `total_usd` as a 2-decimal string (cart subtotal; real charge adds ~$1.40 in fees).
- `failed` means no charge happened → `no_cart` / `over_budget` / `error` kept; `needs_input` → `no_cart`;
  everything else (`sandbox_mode`, `declined`, `approval_required`, `price_changed`) → `declined`. All refund.
- `pending` → `unknown` → engine goes to `reconciling` and calls `inspect_purchase(job_id)` each tick (one
  conversation read). Never refund while pending. The engine passes the job UUID as `request_id`, so a crash or
  retry can't buy twice (records live in `agentcard/.purchases.json` on the machine that ran the job).
- `AgentCardPurchaser` (`agentcard_bridge.py`) shouldn't be used against the real API: it needs a cart currency
  field and merchant tracking that Agentcard doesn't provide, and only counts `status == "order_placed"` as an order
  (the real gum order didn't return that).

### Agentcard credentials
`agentcard/.env` + `.agentcard_tokens.json` (sandbox, user `usr_31f5c35616a38795b45ea5ec`) and `.env.prod` +
`.agentcard_tokens.prod.json` (prod, user `usr_f9fe7033a2dc8fe3896baeb1`). Never committed; currently on Mason's machine.
Refresh tokens are single-use: only ONE machine may use a token file. Hand it over explicitly and stop using
the old copy. If a refresh fails with `invalid_refresh_token`, re-link with `setup_vault.py --connect <Mason's phone>`
(sandbox code 111111) and check the user id (prod must stay `usr_f9fe…`, which has the real card).

### What's left (Masumi side)
1. Fund the Preprod selling and buying wallets (manual faucet; the faucet API key was rejected).
2. Expose the agent publicly (tunnel) and register it on Preprod. Set `CARDANO_CARD_MODE=preprod`,
   `MASUMI_V1_COMPATIBLE=true`, `PAYMENT_SERVICE_URL`, `PAYMENT_API_KEY`, `AGENT_IDENTIFIER`, `SELLER_VKEY`.
3. Prove escrow → payout and escrow → refund on Preprod with `PURCHASE_BACKEND=fake`.
4. `PURCHASE_BACKEND=mason` on Preprod: sandbox decline → refund, then `AGENTCARD_ENV=prod` cheap real order → payout.
   Record tx hashes and order ids for the demo.
- Docs: https://www.masumi.network/dev/masumi/core-concepts/payments and .../refunds-and-disputes

## Rules
- Never commit `.env` or `.agentcard_tokens.json`.
- Keep it simple: non-conversational purchases. If `/buy` asks a follow-up, fail with `needs_input`.