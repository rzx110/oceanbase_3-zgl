"""Bind an explicitly supplied public backend URL into a PRIVATE import copy."""
import argparse
import json
from pathlib import Path
from urllib.parse import urlsplit

import yaml

root = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument("--url", required=True)
parser.add_argument("--name", help="Optional app name for a fresh re-import verification")
args = parser.parse_args()
url = urlsplit(args.url)
if url.scheme != "https" or not url.netloc or url.query or url.fragment or url.username:
    raise ValueError("Supply the public HTTPS backend base URL")
config = json.loads((root / ".local/backend.json").read_text(encoding="utf-8"))
document = yaml.safe_load((root / "workflow/oddity-pawnshop.yml").read_text(encoding="utf-8"))
if args.name:
    document["app"]["name"] = args.name
for value in document["workflow"]["environment_variables"]:
    if value["name"] == "BACKEND_URL":
        value["value"] = args.url.rstrip("/")
    elif value["name"] == "BACKEND_TOKEN":
        value["value"] = config["token"]
target = root / ".local/private-cloud-import.yml"
target.write_text(yaml.safe_dump(document, allow_unicode=True, sort_keys=False), encoding="utf-8", newline="\n")
print("Private import copy saved in .local/private-cloud-import.yml; credentials were not printed.")
