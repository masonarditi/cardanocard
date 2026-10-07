"""Point the hosted Railway runtime at Agentcard sandbox or production, from the local credential files.

  .venv/bin/python scripts/railway_agentcard_env.py --env sandbox|prod [--deploy]

Reads agentcard/.env (sandbox) or agentcard/.env.prod (prod) and the matching token file, sets the Railway
variables (AGENTCARD_ENV, AGENTCARD_CLIENT_ID/SECRET, AGENTCARD_TOKENS_JSON) without printing them, and optionally
redeploys. The token file is seeded onto the /data volume on first boot and rotates there; after handing a token to
Railway, nobody else may use it (single-use refresh tokens).
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[2]
AGENTCARD = ROOT / "agentcard"
STAGING = Path("/private/tmp/cardanocard-hosted-runtime-20261007")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--env", choices=("sandbox", "prod"), required=True)
    parser.add_argument("--deploy", action="store_true", help="railway up --ci from the staging directory afterwards")
    parser.add_argument("--service", default="cardanocard-preprod")
    parser.add_argument("--workspace", default="eztramble's Projects")
    args = parser.parse_args()
    prod = args.env == "prod"
    env_file = AGENTCARD / (".env.prod" if prod else ".env")
    token_file = AGENTCARD / (".agentcard_tokens.prod.json" if prod else ".agentcard_tokens.json")
    creds = dotenv_values(env_file)
    if not creds.get("AGENTCARD_CLIENT_ID") or not creds.get("AGENTCARD_CLIENT_SECRET"):
        sys.exit(f"{env_file} is missing AGENTCARD_CLIENT_ID/SECRET")
    if not token_file.exists():
        sys.exit(f"{token_file} is missing (a linked user token is required; sandbox: parked copy is on Railway already)")
    tokens = json.loads(token_file.read_text())
    if not tokens.get("refresh_token") or not str(tokens.get("user_id", "")).startswith("usr_"):
        sys.exit("token file is not a linked-user token")
    print(f"target: Agentcard {args.env} | user {tokens['user_id']} | org client {creds['AGENTCARD_CLIENT_ID'][:8]}…")
    cmd = ["railway", "variables", "--skip-deploys", "--service", args.service,
           "--set", f"AGENTCARD_ENV={'prod' if prod else ''}",
           "--set", f"AGENTCARD_CLIENT_ID={creds['AGENTCARD_CLIENT_ID']}",
           "--set", f"AGENTCARD_CLIENT_SECRET={creds['AGENTCARD_CLIENT_SECRET']}",
           "--set", "AGENTCARD_TOKENS_JSON=" + json.dumps(tokens, separators=(",", ":"))]
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=ROOT / "masumi")
    if result.returncode != 0:
        sys.exit("railway variables failed (output withheld: may contain secrets)")
    print("railway variables set")
    if args.deploy:
        up = subprocess.run(["railway", "up", "--ci", "--service", args.service, "--workspace", args.workspace],
                            capture_output=True, text=True, cwd=STAGING)
        tail = [l for l in (up.stdout + up.stderr).splitlines() if "Deploy complete" in l or "Deploy failed" in l or "Error" in l]
        print("\n".join(tail) or f"railway up exit {up.returncode}")
    print("after the deploy, confirm with: GET /diagnostics?probe=true (bearer CARDANO_CARD_TOKEN) -> agentcard_env, user_id, agentcard_probe 200")


if __name__ == "__main__":
    main()
