# Integration build status — 6 October 2026

## Delivered locally

- Durable AgentCard bridge around `buy` and `conversation`, matching Mason's API client boundary. Mason's files and existing `purchase()` signature are unchanged.
- Saved request fingerprints, conversation IDs, exact cart hashes, confirmation attempts and order IDs. Duplicate/concurrent requests reuse the record. Unknown writes only inspect; missing references require manual reconciliation.
- Approval continuation only after an explicit `vault_approval_required` + `charge_status=none` response, preserving the cart/address. User approval is a request to retry that permitted confirm; the provider still decides whether approval was granted.
- Budget and $50 card cap before confirm; explicit USD evidence; one cart only. Partial purchases and ambiguous responses never become automatic refunds.
- Order placement and merchant confirmation are separate. A transport must supply verified order ID, currency, final total, merchant and items before fee settlement.
- Synthetic AgentCard response replay: success, sandbox-style decline, budget rejection, approval, clarification, unknown, partial, lost-confirmation recovery. These fixtures are synthetic, not recorded authenticated API responses.
- Browser demo, job resume, event timeline and private evidence export. The demo operates on simulated providers only. No token is embedded in HTML or browser storage.
- Gated Preprod buyer: start, fund, status and refund. Exact V1 payload fields follow the pinned SDK source. Writes are checkpointed and uncertain attempts cannot be automatically repeated.
- Local OpenAPI snapshot tool and credential presence/authentication checker.
- Colored live terminal feed across all saved jobs, plus a standalone AgentCard sandbox runner with explicit handoff, mode introspection, private evidence, local token locking, and durable refresh failure protection.
- Documented conversation recovery: `orders[].order_id` and explicit no-charge `last_checkout` denials after a lost confirmation.

## Verification

103 automated tests passed on 6 October 2026. Tests include the terminal feed, cursor/reconnection handling, sandbox-mode gates, token rotation failure recovery, documented order-ID recovery, and a mocked HTTP run through our agent to simulated refund. They are supplemented by the limited actual sandbox cart/rejection run below; full checkout and on-chain compatibility remain unverified.

AgentCard organization credentials were added to the root `.env`. An OAuth request returned HTTP 200 and an access token. No token was printed or saved. The response did not supply a sandbox flag. The matching sandbox credentials and linked user-token file have now been saved privately in agentcard/. After explicit exclusive handoff, the access token was refreshed once successfully and the rotated token file saved privately.

Browser validation covers submitting a job, simulating escrow funding, viewing order evidence, resuming a saved job, simulating payout, and completing decline/refund. No actual chain or merchant transaction has been performed.

## Next external prerequisites

1. **Cardano:** the local node health and authentication now pass. The running OpenAPI matches the pinned 0.22.0 source exactly. Preprod `Web3CardanoV1` purchasing and selling wallets were discovered; both have **0 test ADA** as of this check. Agent identifier, seller key and application payment API key are not configured. Fund both test wallets, register the agent and complete live contract acceptance. The compatibility flag remains off; matching schemas alone do not prove settlement.
2. **AgentCard:** Mason confirms the sandbox card is already linked. Receive his matching `.env` and `.agentcard_tokens.json` into `agentcard/`, keep them ignored/private, and obtain an explicit single-operator handoff before authenticated user calls. Do not run `setup_vault.py`. Token refresh rotates the shared token; reads can refresh too. If access is invalidated, stop and tell the user so Mason can re-link. The matching files are now present and ignored by Git; the user confirmed exclusive handoff for our sandbox run.
3. **Response fixtures:** obtain sanitized real sandbox responses, including cart currency and conversation/order shapes. The public cart examples omit currency, so the bridge refuses a network cart until USD can be established through an authoritative mapping. Implement the order-tracking mapping from actual documentation/fixtures; never synthesize merchant confirmation from `order_placed`.
4. **Acceptance:** run actual Preprod success/payout and decline/refund using simulated merchant outcomes. Then run the combined sandbox path. Capture actual transaction hashes and independently verify them.
5. **Scope:** sandbox only. A confirm ends in `sandbox_mode` and exercises the service-fee refund path. Use explicitly simulated success to test payouts. Production merchant purchases are outside the current authorized scope.

The new `python -m cardano_card.sandbox_run` preflight now passes file-presence checks: matching credentials and user tokens are present with owner-only permissions. Subsequently, one token refresh and an actual cart request succeeded; see the run record below. The user confirmed exclusive handoff and explicitly approved export of the sample checkout identity/address/phone. See AGENTCARD_SANDBOX_RUN.md for the prepared command and the unresolved cart-currency check. The real cart-to-budget-rejection branch is verified; sandbox confirmation and on-chain settlement are not yet verified.

One request to the official Preprod faucet for the seller wallet returned HTTP 200 with application error `FaucetWebErrorInvalidApiKey`. It did not confirm funding. A valid faucet key or manual faucet funding is needed; no Cardano transaction has been submitted by our agent.

## Remaining scope

Registry discoverability, MIP-003 client conformance and signed input acknowledgements, hosted access, multiple customers, independent blockchain evidence verification, operator resolution of ambiguous writes, and x402 are unfinished. No claim of live end-to-end operation or hackathon completion is made.

Current status: local integration implemented and tested; external acceptance still pending.

## Shared sandbox token operating rule

The lower-level network transport now refuses all authenticated buy/conversation/order calls without an explicit exclusive-access callback. This is a fail-closed local guard, not a cross-machine lock: the handoff must come from coordination with Mason. Do not repeatedly probe a rejected/rotated connection token or re-enroll the card. No message has been sent to Mason.

## Actual AgentCard sandbox result

At 12:30 SGT on 6 October 2026, job `f5db882e-111a-4d3d-87cf-58fd09a80491` completed a simulated service-fee refund after rejecting an actual sandbox cart. AgentCard reported an estimate of 1,241 cents and an authorization ceiling of 2,348 cents, above the requested 2,000-cent cap. The integration now checks the ceiling, persists candidate carts, and recovers validation-paused carts via reads. Zero checkout confirmations were sent; no merchant order or Cardano transaction was created. Currency remains unverified, and the sandbox_mode confirmation branch was not exercised. Rotated user tokens are saved locally; Mason needs that current file before resuming.
