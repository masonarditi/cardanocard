# Cardano Card

A purchasing agent that lets other agents buy goods online through AgentCard, with Cardano service-fee payments handled by Masumi.

## How it works

1. A buyer agent submits an item request, spending limit, and delivery details.
2. Cardano Card prepares a cart and requests approval of its full USD authorization ceiling and separate test-ADA escrow price.
3. The buyer locks the approved payment in Masumi escrow on Cardano.
4. AgentCard handles checkout using the authorized card.
5. Confirmed order evidence supports service-fee settlement. A definitive purchase failure starts the refund flow; uncertain outcomes are reconciled before further action.

Merchant spending and the Cardano service fee are separate payments.

## What we’re building

- Agent-to-agent shopping requests with spending limits.
- AgentCard cart creation, checkout, and order evidence.
- Masumi escrow, service-fee payouts, and refunds.
- Saved progress and recovery to prevent duplicate checkout attempts.
- A live terminal feed showing each purchase and payment step.

## Current status

The staged quote, approval, escrow, checkout and settlement flow is implemented and tested locally. The receiving wallet is configured on the local Preprod node. Live Cardano settlement and the combined merchant purchase remain unproven pending funding, registration and Mason’s v2 adapter.

For setup and usage, see the [terminal guide](docs/TERMINAL_RUNBOOK.md) and [sandbox guide](docs/AGENTCARD_SANDBOX_RUN.md).
For resumable registration and real testnet acceptance, see the [Preprod acceptance guide](docs/PREPROD_ACCEPTANCE.md).

## Setup

Run integration commands from `masumi/`. Mason's AgentCard client remains in the sibling `../agentcard/` directory.

```sh
cd masumi
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-local.lock
.venv/bin/python -m pip install --no-deps -e .
.venv/bin/python scripts/init_local.py
.venv/bin/python -m pytest
.venv/bin/cardano-card
```

Python 3.11 or later is required. Configuration, runtime data, and credentials from the previous checkout are not included. The default run uses simulated providers. The optional pinned Masumi SDK is installed with `.venv/bin/python -m pip install -e '.[masumi]'` only when preparing Preprod acceptance.

See [build status](BUILD_STATUS.md) for the results recorded in the original local checkout and remaining acceptance work. AgentCard sandbox commands require matching private files in `../agentcard/` and a fresh exclusive handoff.

## Staged terminal demo

From `masumi/`, start a dedicated simulation database:

```sh
CARDANO_CARD_MODE=local PURCHASE_BACKEND=staged_fake CARDANO_CARD_DB=data/staged-jobs.db .venv/bin/cardano-card
```

In another terminal:

```sh
.venv/bin/python -m cardano_card.terminal demo --scenario success
.venv/bin/python -m cardano_card.terminal demo --scenario declined
.venv/bin/python -m cardano_card.terminal live --ascii
```

The demo driver only funds and approves simulated jobs. For a real job, `watch JOB_ID` stays read-only; approve its displayed revision with `approve-quote JOB_ID --quote-hash INPUT_SCHEMA_HASH`. See [integration contract](docs/INTEGRATION_PLAN.md) before connecting Mason’s adapter. Keep Blockfrost Preprod; Base/x402 and card issuance are deferred.
