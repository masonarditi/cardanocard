"""Read-only local setup check. Reports presence/status, never secret values."""
import json
import subprocess
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from dotenv import dotenv_values

root = Path(__file__).resolve().parents[1]
path = root / "infra/masumi/.env"
values = dotenv_values(path) if path.exists() else {}
ready = True
for key in ("POSTGRES_PASSWORD", "ADMIN_KEY", "ENCRYPTION_KEY"):
    present = bool(values.get(key))
    ready &= present
    print(f"{key}: {'set' if present else 'missing'}")
chain_ok = bool(values.get("NOWNODES_API_KEY")) or values.get("BLOCKFROST_API_KEY_PREPROD", "").startswith("preprod")
print(f"chain evidence provider: {'NOWNodes (ada-testnet)' if values.get('NOWNODES_API_KEY') else 'Blockfrost Preprod' if chain_ok else 'missing'}")
if values.get("BLOCKFROST_API_KEY_PREPROD") and not values["BLOCKFROST_API_KEY_PREPROD"].startswith("preprod"):
    print("Blockfrost key does not have the expected Preprod prefix; check its project network.")
ready &= chain_ok
try:
    result = subprocess.run(["docker", "info", "--format", "{{.ServerVersion}}"], capture_output=True, text=True, timeout=15)
    running = result.returncode == 0
except (FileNotFoundError, subprocess.TimeoutExpired):
    running = False
print(f"Docker daemon: {'available' if running else 'unavailable'}")
ready &= running

def check(label, route, authenticate=False):
    headers = {"token": values["ADMIN_KEY"]} if authenticate and values.get("ADMIN_KEY") else {}
    try:
        with urlopen(Request("http://127.0.0.1:3001/api/v1/" + route, headers=headers), timeout=3) as response:
            data = json.load(response)
            ok = response.status == 200 and data.get("status") == "success"
    except (HTTPError, URLError, TimeoutError, ValueError, OSError):
        ok = False
    print(f"{label}: {'passed' if ok else 'not ready'}")
    return ok

health = check("Local Payment Service health", "health")
auth = check("Local Payment Service authentication", "api-key-status", True) if health else False
if not health:
    print("Local Payment Service authentication: not checked")
if not (ready and health and auth):
    raise SystemExit(1)
print("Node preflight passed. SDK compatibility, wallet balances, and escrow tests remain separate checks.")
