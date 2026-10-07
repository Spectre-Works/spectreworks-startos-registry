#!/bin/bash
# Promote one approved package version. Does not rehost the .s9pk.
# Usage: promote-package.sh <package-id> <version-key>
set -euo pipefail
root=$(cd "$(dirname "$0")/.." && pwd)
id=${1:?package id}
version=${2:?version}
source=https://start9.mempool.guide
dest=https://registry.startos.spectreworks.io
start-cli registry package promote --from "${source}" --to "${dest}" "${id}" "${version}"
entry=$(start-cli --registry "${dest}" registry package get "${id}" full --format json)
python3 - "${root}/state/catalog-lock.json" "${id}" "${version}" "${source}" "${entry}" << 'PY'
import json, sys, datetime
lock_path, package_id, version, source, raw = sys.argv[1:]
entry = json.loads(raw)
lock = json.load(open(lock_path))
urls = []
commitment = None
def walk(node):
    global commitment
    if isinstance(node, dict):
        if "urls" in node and isinstance(node["urls"], list):
            urls.extend(node["urls"])
        if commitment is None and isinstance(node.get("commitment"), dict):
            commitment = node["commitment"]
        for value in node.values():
            walk(value)
    elif isinstance(node, list):
        for value in node:
            walk(value)
walk(entry)
record = {
    "packageId": package_id,
    "version": version,
    "sourceRegistry": source,
    "urls": urls,
    "commitment": commitment,
    "approvedAt": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    "notes": "",
}
lock["packages"] = [item for item in lock["packages"] if not (item.get("packageId") == package_id and item.get("version") == version)]
lock["packages"].append(record)
json.dump(lock, open(lock_path, "w"), indent=2)
open(lock_path, "a").write("\n")
print(json.dumps(record, indent=2))
PY
