# Cardano Card

A purchasing agent that lets other agents buy goods online through AgentCard, with Cardano service-fee payments handled by Masumi.

## How it works

1. A buyer agent submits an item request, spending limit, and delivery details.
2. The buyer locks the service fee in Masumi escrow on Cardano.
3. Cardano Card builds a merchant cart and checks its full authorization ceiling against the spending limit.
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

The local lifecycle and a real AgentCard sandbox cart-to-budget-rejection flow are working. Full Cardano Preprod settlement and combined end-to-end acceptance are still being validated.

For setup and usage, see the [terminal guide](docs/TERMINAL_RUNBOOK.md) and [sandbox guide](docs/AGENTCARD_SANDBOX_RUN.md).

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
