"""Open the loopback demo; pass the local token in a fragment, never a server log."""
import os
import webbrowser
from pathlib import Path
from urllib.parse import urlencode

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")
token = os.getenv("CARDANO_CARD_TOKEN", "")
if len(token) < 24:
    raise SystemExit("Initialize the local .env first")
port = int(os.getenv("CARDANO_CARD_PORT", "8080"))
webbrowser.open(f"http://127.0.0.1:{port}/#" + urlencode({"token": token}))
print("Opened local demo. The token is removed from the URL after the page loads.")
