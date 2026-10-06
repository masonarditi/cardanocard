"""Prepare local node secrets without changing existing credentials or printing them."""
import os
import secrets
from pathlib import Path

root = Path(__file__).resolve().parents[1]
target = root / "infra/masumi/.env"
if target.exists():
    raise SystemExit("infra/masumi/.env already exists; preserved all existing values")
content = target.with_name(".env.example").read_text()
for key in ("POSTGRES_PASSWORD", "ADMIN_KEY", "ENCRYPTION_KEY"):
    # Hex is safe in the PostgreSQL connection URI and supported by upstream key-length requirements.
    content = content.replace(key + "=\n", key + "=" + secrets.token_hex(32) + "\n", 1)
fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(fd, "w") as handle:
    handle.write(content)
print("Prepared private local database/admin/encryption credentials in infra/masumi/.env.")
print("Add BLOCKFROST_API_KEY_PREPROD to that file. No services or wallets have been created.")
