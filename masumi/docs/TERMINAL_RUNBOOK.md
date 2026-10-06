# Terminal workflow

Run from the `masumi/` directory of this repository. The API stays bound to loopback, and its frontend is disabled by default. Keep the API process running in one terminal; use another for visibility.

```sh
PURCHASE_BACKEND=replay CARDANO_CARD_DB=data/replay-demo.db .venv/bin/cardano-card
```

The server prints every saved lifecycle step immediately, with Singapore time, event number, job ID, phase, message and explicit escrow/purchase modes. Output is flushed as each database checkpoint commits. Set `CARDANO_CARD_LIVE_OUTPUT=false` to silence this server view.

For a separate continuous feed across **all jobs**, run:

```sh
.venv/bin/cardano-card-terminal live
```

Stages have emoji, plain-language labels, and terminal colors: cyan for work in progress, yellow for waiting, green for completion, and red for review. Each step has its own block with the explanation and job reference underneath. Simulation labels stay visible on every step. Colors enable automatically in an interactive terminal and respect `NO_COLOR`.

For plain ASCII output: `cardano-card-terminal live --ascii --color never` (use `.venv/bin/` as above). Force colors with `--color always`. The server uses the same layout; set `CARDANO_CARD_ASCII=true` to replace emoji there.

It shows the latest 20 saved steps, then checks for new events every half second. It stays open after a job completes and includes jobs created later. Ctrl-C stops only the viewer. `live --after 0` replays all saved history; `live --after 42` resumes after event 42. Optional `--timeout 60` stops the viewer after a minute. The authenticated `/events` endpoint returns ordered, bounded pages, so fast transitions are retained even between polls. A connection interruption retries reads using the last cursor; a cursor ahead of the current database stops with a reset explanation. The feed contains lifecycle messages and mode labels, not request bodies or provider credentials.

## Inspect or demonstrate

```sh
.venv/bin/python -m cardano_card.terminal status
.venv/bin/python -m cardano_card.terminal demo --scenario success
.venv/bin/python -m cardano_card.terminal demo --scenario declined
.venv/bin/python -m cardano_card.terminal demo --scenario timeout_recovered
.venv/bin/python -m cardano_card.terminal watch JOB_ID --timeout 3600
```

`status` lists recent jobs. `watch` makes only reads, works for simulation or Preprod, and can be stopped with Ctrl-C without stopping the job. It prints a new line when the phase or escrow state changes, plus each event once. `demo` creates a synthetic job and performs simulated funding, input, payout/refund; it refuses non-simulated providers. Add `--evidence work/a-unique-filename.json` to save a final evidence bundle; an existing file is never overwritten.

Other scenarios: `over_budget`, `approval`, `needs_input`, `unknown`, `partial`. Use `--reject` for a simulated approval rejection. Unknown/partial cases end the CLI timeout with exit code 2 and retain the existing job, rather than retrying a purchase or refunding it.

The output separates:

- `STATE`: durable job and escrow phase.
- Timestamped events in SGT: exact progression and recovery steps.
- `ACTION simulated`: an explicitly fake operation.
- `RESULT`: order/fee outcome, never a claim of physical delivery.
- `PROOF`: node-reported transaction count vs independently verified chain evidence.

## Inspect the Masumi node

```sh
.venv/bin/python scripts/check_masumi.py
.venv/bin/python scripts/masumi_status.py --balances
.venv/bin/python scripts/inspect_masumi_openapi.py http://127.0.0.1:3001/api-docs
```

These tools do not create payments or request wallet mnemonics. The wallet tool reads the Preprod payment source and saves public wallet references to ignored `work/preprod-wallets.json`. Balance checks use the Preprod Blockfrost key without printing it.

## Actual Preprod acceptance

After registration, funded wallets, OpenAPI/auth/hash checks and the fixed test fee are configured, use `preprod_buyer start/fund/status/refund` as documented in PREPROD_BUYER.md. Follow each job with `terminal watch JOB_ID`. Native Cardano contract windows cannot be simulated away: this node waits until unlock plus a ten-minute clock margin before automatically starting payout. Our short demo deadlines make that roughly 46 minutes from payment creation, plus processing and confirmations.

The merchant purchase stays simulated for these first chain tests. Mason's shared AgentCard user token is not needed and must not be refreshed without his exclusive handoff. No commits, pushes, hosted deployment or production purchases are part of this workflow.
