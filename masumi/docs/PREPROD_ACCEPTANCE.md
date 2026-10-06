# Preprod acceptance from the terminal

Run from `cardanocard/masumi` on the `ezra` branch. This workflow uses real Cardano Preprod service-fee escrow. `payout` and `refund` use explicitly simulated merchant outcomes; `sandbox-refund` uses AgentCard sandbox. Merchant spending and the service fee are separate payments.

## Restore and inspect

The new workspace has its own `.venv`, private integration configuration, the original node credentials, and a snapshot of the paused sandbox database. AgentCard tokens were deliberately not copied. The existing containers and database volume are preserved; do not generate new encryption credentials for the existing volume. The archived replay process remains separate and was not restarted.

```sh
.venv/bin/python -m cardano_card.preprod_setup inspect
```

The default command only reads the local node and Blockfrost Preprod. It verifies the reviewed Masumi 0.22.0 OpenAPI, reports public buyer/seller addresses and balances, and lists registration status. The V1 purchase endpoint cannot select a buyer wallet, so setup rejects ambiguous multiple purchasing wallets.

Fund the reported buyer and seller addresses with at least 20 test ADA each. The addresses are unchanged by the repository migration. Balance checks do not request funds from a faucet.

## Register and configure

```sh
.venv/bin/python -m cardano_card.preprod_setup register --execute
.venv/bin/python -m cardano_card.preprod_setup inspect
# Wait for RegistrationConfirmed before continuing.
.venv/bin/python -m cardano_card.preprod_setup configure --execute
```

Without `--execute`, register/configure inspect and describe readiness. Writes are checkpointed before sending. A lost response stays unknown; repeating the command reconciles matching registration metadata or stops instead of issuing a duplicate write. Keep the same `data/preprod-setup.db`.

The default registration is a private local test identity with a 10-test-ADA fixed service fee and loopback API URL. It does not make the agent publicly reachable. Separate buyer and seller API keys are capped at 100 test ADA and restricted to Preprod. Those keys are not wallet-specific. `.env.preprod` is written atomically with owner-only permissions. Schema compatibility flags certify the reviewed wire format only, not live settlement.

## Run a saved acceptance case

Create an ignored `work/acceptance-input.json` containing the input object (`ask`, `max_total_usd`, and `address`). Generate a unique 26-character lowercase hexadecimal request ID once, record it, and reuse that same ID, input file, case and database on every resume. The runner permits one request per acceptance database. Do not change the database to work around an uncertain result.

```sh
# Presence-only preflight; no provider calls or payments.
.venv/bin/python -m cardano_card.acceptance --case payout

# Replace REQUEST_ID with the saved ID; input is sent only to simulated purchasing.
.venv/bin/python -m cardano_card.acceptance --case payout \
  --request work/acceptance-input.json --request-id REQUEST_ID \
  --execute --timeout 3600

# Use a different saved ID for the separate simulated-decline case.
.venv/bin/python -m cardano_card.acceptance --case refund \
  --request work/acceptance-input.json --request-id REFUND_REQUEST_ID \
  --execute --timeout 3600
```

The runner verifies live registration, fixed fee, contract, wallet identity, schema and restricted API keys before creating a payment. New funding needs fee plus a buyer fee buffer and seller transaction funds. Resume after an existing funding attempt does not demand that original balance again. Buyer payment identity is checked before the coordinator may start purchasing. A timeout, crash or unknown payment response never triggers another funding POST.

For fixed-price V1 agents the buyer validates the quoted fee locally and omits `Amounts` from the purchase request; the node reads pricing from registration and rejects that field. Failed requests retain a safe HTTP status in evidence. If funding is unconfirmed and the buyer record is absent, the runner stops with `BLOCKED` and saves `INCOMPLETE` evidence instead of waiting through settlement deadlines. An HTTP error or missing record alone is not authorization to repay: reconcile node records, transaction history and chain evidence first. Expired terms require a separately recorded new case after definitive reconciliation.

The terminal prints durable lifecycle events plus a periodic waiting message. `--ascii` disables emoji. Stop with Ctrl-C and resume with the exact same command. Real contract deadlines remain in force: the current payout window begins roughly 36 minutes after payment creation, followed by processing and confirmations. A short CLI timeout does not cancel an escrow or purchase.

## AgentCard sandbox refund

Restore only the current matching `../agentcard/.env` and token file after coordinating exclusive access with Mason. Do not run vault enrollment. Resolve the archived gum attempt first: its cart request has no usable conversation ID and must not be duplicated. The acceptance runner checks the migrated prior database and blocks new checkout while that uncertainty remains.

```sh
.venv/bin/python -m cardano_card.acceptance --case sandbox-refund \
  --request work/approved-sandbox-input.json --request-id SANDBOX_REQUEST_ID \
  --execute --exclusive-handoff --timeout 3600
```

This mode verifies `test_mode=true` before AgentCard calls, uses the existing durable cart/ceiling checks, and requires a definitive `sandbox_mode` decline for the intended scenario. A generic failure or budget rejection can produce a refund but does not pass sandbox-confirmation acceptance. Production credentials and real merchant purchasing are outside this command's scope.

## Evidence and completion

Each run saves private evidence in `work/preprod-evidence/JOB_ID.json`. Buyer and seller transaction references are combined, then inspected independently through the fixed Blockfrost Preprod endpoint. The evidence states what was checked and what remains unverified. A node-reported terminal status or a transaction existing on chain alone is insufficient for `PASS`.

`PASS` requires the intended terminal branch, funding proof, result proof for payout, and matching independent settlement verification. Unsupported transaction shapes, missing evidence or unavailable Blockfrost data remain `INCOMPLETE` with exit code 2. Node completion and proof completion are separate fields. Nothing in an escrow proof establishes a real merchant purchase in the simulated cases.

Existing safety tests use mocked providers and synthetic chain fixtures. Wallets are funded and agent registration is confirmed. Live escrow acceptance remains pending until the real payout/refund runs complete. The payment container currently needs the documented Preprod protocol-11 cost-model patch; see LOCAL_MASUMI_SETUP.md before recreating it.
