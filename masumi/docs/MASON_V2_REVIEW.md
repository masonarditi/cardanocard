# Review notes for Mason — `agentcard/purchase_v2.py` and `staged_acceptance.py` (2026-10-06, Ezra's side)

Nothing in `agentcard/` v2 or `staged_acceptance.py` was changed from our side. These came out of an offline audit
(driving `StagedEngine` + `FakeEscrow` against `purchase_v2` with monkeypatched `buy`/`conversation`). Ranked by what
would bite in the live demo.

## 1. HIGH — a real settled order pays out only if the ledger says `status: "settled"` with an integer `total_cents`
- `_inspect` returns `confirmed` only for `orders[0].status == "settled"` (`purchase_v2.py` ~L205) and `_wire` copies
  `order["total_cents"]` as-is.
- Every fixture is invented (`tests/test_purchase_v2.py` `SETTLED`); the repo holds no real `GET /buy/conversations/{id}`
  order record. v1 never looked at the order status, so the gum order never exercised this.
- Proven offline: status `placed` → job stuck in `processing`; `total_cents` missing or a float (`270.0`, StrictInt) →
  stuck in `reconciling`. Each loops until `submitResultTime`, then `manual_review`: no payout **and** no refund.
- Ask: capture one real conversation read for the gum order (`conversation_id` is in your prod ledger) and pin the
  fixture to it before the live run. If the real status is `placed`/`confirming` for a long time, treat that as
  `confirmed` once `charge_status` is captured, or we'll miss the payout window.

## 2. MED — a failed order after confirm never refunds automatically
- `_wire` sets `no_purchase = not order_id`. A `funding_failed` order carries an `order_id`, so
  `CoordinatedPurchaser.normalize` treats it as a possible purchase → `reconciling` forever (no refund).
- `PURCHASE_V2.md` says `failed_no_purchase` always has `no_purchase: true`; not for this case.
- Suggest: `no_purchase = (charge_status == "none")` and only then `failed_no_purchase`.

## 3. MED — `max_total_usd` in the request file must cover the authorization ceiling
- `StagedEngine.accept_quote` rejects a quote whose `authorization_ceiling_cents` > `max_total_usd`. Ceiling ≈ subtotal
  + $11, so `"10.00"` for gum → `quote_rejected` (proven). Use ≥ ~$12.10 for prod gum, ≥ ~$23.50 for the sandbox
  12-pack. Not documented anywhere the operator will look.
- Also: with `max_total_usd` > $50 and a ceiling between $50 and that, v2 reports the cart over budget but still
  returns `prepared`; the engine accepts, the buyer funds escrow, then gets refunded with 0 confirms (proven). Wasted
  escrow round — better to return `failed_no_purchase` from `prepare` when the ceiling exceeds `CARD_LIMIT_CENTS`.

## 4. LOW–MED — `staged_acceptance.py` gaps
- No console script in `pyproject.toml` (run with `python -m cardano_card.staged_acceptance`).
- Skips the `--exclusive-handoff` and credential-file checks that `acceptance.check_mason_case` enforces.
- Auto-approves the quote, including on `live`.
- `STOP` lacks `awaiting_input`: if the vault asks for approval the run spins until the 3600 s timeout because nothing
  calls `provide`.

## 5. LOW
- `api.py` `staged_module` backend doesn't add `agentcard/` to `sys.path` (the `mason` backend does) — operator must set
  `PYTHONPATH`; documented only in `PURCHASE_V2.md`.
- `PURCHASE_V2.md` test count ("22 unit tests") is stale: 52 agentcard tests now, including v1's.

## What was changed on the v1 side (same contract, already on `ezra`)
`purchase.py` returns `failed` only when Agentcard says nothing was charged (`charge_status: "none"`, `sandbox_mode`,
approval declines). Missing/settling checkout, `partially_placed`, any HTTP error after the confirm, a missing ledger
record or an unreadable ledger → `pending` (engine inspects, never refunds). Ledger writes are atomic. `agentcard.py`
refreshes tokens atomically under a lockfile + pending marker (prod has its own names). Tests:
`agentcard/tests/test_purchase_v1.py`.
