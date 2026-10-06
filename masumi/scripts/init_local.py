"""Create a local simulator configuration without printing its private caller token."""
import os
import secrets
from pathlib import Path

target = Path(".env")
content = Path(".env.example").read_text().replace("CARDANO_CARD_TOKEN=\n", "CARDANO_CARD_TOKEN=" + secrets.token_urlsafe(32) + "\n")
try:
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
except FileExistsError:
    raise SystemExit(".env already exists; preserved existing configuration")
with os.fdopen(fd, "w") as stream:
    stream.write(content)
print("Created .env for local simulation. Caller token saved privately.")
