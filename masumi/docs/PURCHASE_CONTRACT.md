# Purchasing handoff — Ezra ↔ Mason

Mason owns merchant purchasing; Ezra owns the service-fee escrow and caller experience.

## Proposed integration entry point (not Mason’s current signature)

```python
def purchase(ask: str, max_total_usd: float, address: dict, *, request_id: str) -> dict:
    ...
```

Address fields: `street`, `city`, `state`, `zip`, `phone`, `name`. The Masumi form presents these flat and the adapter reconstructs the address dictionary.

Success: `status=success`, `order_id`, `total_usd`, `merchant`, nonempty `items` list. Success means a merchant-confirmed order. Use decimal values/cents internally; serialize money as a two-decimal string where possible. The adapter accepts numeric or string totals and enforces decimal validity.

Definitive failure: `status=failed`, `reason` in `no_cart`, `over_budget`, `declined`, `error`. Use this only when no purchase occurred. The local simulator also proposes `cancelled` for a verified cancellation.

Pending: `status=pending`, `reason` in `approval_required`, `needs_input`, `processing`, `unknown`; optional `conversation_id`, `approval_url`, `message`.

## Current implementation

Mason's pulled `purchase(ask, max_total_usd, address)` does not accept request_id or expose inspect_purchase. The signature above remains our proposal, not a claim that both sides agreed to it.

Our `AgentCardPurchaser` now owns durable checkpoints around Mason's lower-level `buy` / `conversation` boundary. `MasonClientTransport` can load his trusted client module with sandbox verification and order-tracking callbacks. The application enables only synthetic replay; real sandbox fixture mapping and account verification remain necessary. Mason's existing files and public signature were not changed.

## Two additions to agree if using the high-level function

The original function can request information but cannot receive it. Proposed backward-compatible keyword:

```python
def purchase(ask, max_total_usd, address, *, request_id, response=None) -> dict:
    ...

def inspect_purchase(*, request_id: str) -> dict:
    """Read existing purchase state only. Never start or confirm a new checkout."""
```

`response` is either `{"approved": true/false}` or `{"answer": "..."}` in the first demo. A UI approval boolean is not proof that AgentCard approved a charge: Mason must independently verify the provider's approval state and approved cart before spending. The exact continuation schema still needs Mason's agreement.

`inspect_purchase` is necessary after a crash or ambiguous network response. Returning pending/unknown is correct when evidence is missing. It must not create a fresh conversation/cart/charge. Polling an existing provider conversation is allowed. A missing local record does not by itself prove that a remote charge failed.

## Invariants

- Ezra passes the stable server-created job UUID as `request_id` on every invocation.
- Mason durably maps request ID → immutable request fingerprint → conversation → approved cart → order.
- Duplicate/concurrent calls must not place duplicate orders. Changed original inputs under an existing ID are rejected. Clarification is separate from immutable budget/address/intent.
- Final cart total includes all taxes/shipping; check before charging. A confirmed over-budget order is an incident, never a no-purchase failure.
- Provider timeouts return pending/unknown. The caller must inspect before considering any retry.
- Budget/address/cart changes need an explicit new approval protocol; they are outside this first demo.
- Mason sets bounded I/O timeouts. Cancelling a Python thread does not cancel a remote charge.
- Real card approval occurs through the provider's secure flow. No card numbers or credentials appear in prompts, job results, or logs.
- Cancellation is definitive only after the provider confirms the purchase did not occur and cannot continue unattended.
- Completion means order placed/confirmed, not physical delivery or eventual merchant refund.

## Mason's handoff checklist

- Module + pinned dependencies + `.env.example`, with no secrets.
- Sample success, decline, over-budget, approval, input, processing, and unknown responses.
- Details of provider approval verification and cart binding.
- Evidence that retries and restarts do not create duplicate purchases.
- Supported merchant and US shipping-address requirements.
- A read-only reconciliation method and definitive cancellation behavior.
- One separately authorized real-purchase proof when production access is ready.

No message has been sent to Mason. This is a local contract proposal for review.


## Mason sandbox handoff (latest operating instructions)

Use the matched sandbox `.env` and `.agentcard_tokens.json` supplied by Mason in `../agentcard/`; neither may be committed. The card is already linked: never run `setup_vault.py`. Only one operator may use the shared connection at a time. Obtain an explicit handoff before calling `purchase()`, `buy`, conversation reads, order tracking, or anything that may refresh the user token. A local lock does not coordinate with Mason’s machine. On a rejected/rotated token, stop and tell the user; Mason will re-link. Do not automatically retry authentication or run enrollment.

Sandbox confirmation produces `sandbox_mode`, which exercises the refund path. Payout demonstrations use fake success and retain simulation labels. Root organization credentials alone are not a substitute for this matched handoff.
