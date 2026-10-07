# Hosted Preprod registration

The public registration endpoint is deployed in Railway workspace **eztramble's Projects**:

- URL: https://cardanocard-preprod-production.up.railway.app
- Project: `b7379319-a841-459c-ae44-e1335c7eee89`
- Environment: `2a526083-0e1d-42f9-9605-274c57bce885` (Railway's environment name is production; the application is explicitly Cardano **Preprod**)
- Service: `0f86cbfb-9a3f-4419-b579-253f2c36fb07`
- Verified successful deployment: `031ec0b3-874f-41ef-bca0-c363995476e8`

The deployed module is `cardano_card.hosted_api`. It serves `/healthz`, `/input_schema`, and an honest `/availability` response with `status: unavailable`. Job creation, input submission and status return 503. It does not load secrets, instantiate payment/card providers, run a worker, or store requests. No private local API is tunneled.

Only `pyproject.toml`, `Dockerfile.public` (as `Dockerfile`), `railway.public.json` (as `railway.json`), and the package's `__init__.py`, `models.py`, and `hosted_api.py` were staged for upload, plus a `.dockerignore`. The staged release lives at `/private/tmp/cardanocard-preprod-public-20261006`. Never upload the workspace wholesale or copy `.env`, wallet recovery, runtime databases or AgentCard tokens into that release.

Masumi accepted the hosted registration under **ezzycoin**, user ID `84dd7b06-95bb-4835-882b-75537cd231dd`:

- Agent: **Cardano Card Preprod**
- Hosted ID: `544fcfcd-29e5-4453-9a04-ac6554956d56`
- Network: Preprod
- Requested pricing: Dynamic
- Payout: existing dedicated Preprod receiving address
- Latest checked state: RegistrationRequested in both SaaS and the underlying registry, verification PENDING, no agentIdentifier or transaction yet. Registry ID: `cmuwdqxzm00sk1ytq7t700l44`; `lastCheckedAt` is null.
- The completion API returned 202 with “Wallet not yet funded.” Its official route uses that message for all pending results, including registry synchronization and confirmation waits. It does not establish the cause by itself.
- Separate authenticated Blockfrost Preprod reads returned 404/no on-chain history for the registry's minting and recipient addresses. No test ADA was sent to either address. Masumi needs to confirm its managed funding and registration-worker state.
- Validation: 341 local tests passed; live job/input/status requests return 503, and private event/environment routes return 404.

The hosted API key is saved only in ignored `.env.hosted` with owner-only permissions. Submission and completion receipts are private under `work/hosted-registration-*.json`. Reconcile the existing agent by ID before any retries; do not submit another POST `/api/agents`.

## Remaining execution work

This is a registration deployment, not a working cloud checkout. The local API and payment node still run on the Mac. Masumi SaaS returned a **Web3CardanoV2** source, whereas the tested local adapter is pinned to V1. The underlying payment-node registry correctly records Dynamic pricing; the SaaS agent response incorrectly/differently displays Free on its copy of that source. Treat this as an unresolved presentation/synchronization discrepancy, not a reason to overwrite the canonical Dynamic registry entry. Recheck final on-chain metadata after confirmation.

Before accepting hosted jobs, resolve V2/SDK compatibility and payment terms, connect a supported hosted escrow backend, provide durable job storage and caller authentication, and run payout/refund acceptance. Connect Mason's staged adapter only after that; retain separate authorization for real card purchases. Do not replace local `.env.preprod` identifiers with this hosted ID without migrating the associated payment source and wallet configuration.

## Read-only readiness command

Run from `masumi/`:

```sh
.venv/bin/python -m cardano_card.hosted_readiness \
  --agent-id 544fcfcd-29e5-4453-9a04-ac6554956d56 \
  --user-id 84dd7b06-95bb-4835-882b-75537cd231dd \
  --url https://cardanocard-preprod-production.up.railway.app \
  --payout-address addr_test1qpgq4gf9fmzg3gkdnp9jdtujtscr2qdms07tmtp8aqz5ykc7uvlelkeuqsgctx9n4en6xmkf4qhqkumzcc3qwt4845hqsxwadj
```

This reads `.env.hosted`, checks account ownership, exact deployment/payout, both registration states and identifiers, canonical Dynamic pricing and the unique configured Preprod V2 source. It never submits payments, retries registration, or enables execution. It keeps registration readiness separate from unimplemented/unverified execution readiness. The SaaS key is sent only to `app.masumi.network`; the public Railway probe uses a separate client without that key. Redirects are not followed.

Next: [V2 implementation checklist](HOSTED_V2_PLAN.md). If registration remains queued, use the [prepared support note](MASUMI_REGISTRATION_SUPPORT.md); it has not been sent.
