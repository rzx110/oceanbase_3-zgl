"""Create a fresh free seekdb D0 instance, saving credentials only locally."""
import argparse
import json
import os
from pathlib import Path
import urllib.request

parser = argparse.ArgumentParser()
parser.add_argument("--output", type=Path, default=Path(__file__).resolve().parents[1] / ".local/d0.json")
args = parser.parse_args()
if args.output.exists():
    print("Configuration already exists. For an expired instance, choose a new --output path and set SDD_SEEKDB_CONFIG.")
    raise SystemExit(0)
request = urllib.request.Request("https://d0.seekdb.ai/api/v1/instances", data=b"", method="POST")
with urllib.request.urlopen(request, timeout=45) as response:
    config = json.load(response)
for key in ("host", "port", "username", "password", "database"):
    if key not in config:
        raise ValueError("D0 response is missing database connection fields")
args.output.parent.mkdir(parents=True, exist_ok=True)
args.output.write_text(json.dumps(config), encoding="utf-8")
os.chmod(args.output, 0o600)
print("Created real seekdb D0 instance; credentials saved locally, not printed.")
print("Expires:", config.get("expires_at"))
