#!/bin/bash
# Usage: inspect-package.sh <package-id> [version-key]
set -euo pipefail
root=$(cd "$(dirname "$0")/.." && pwd)
id=${1:?package id}
key=${2:-}
python3 - "${root}" "${id}" "${key}" << 'PY'
import json, sys
root, package_id, version_key = sys.argv[1:]
index = json.load(open(f"{root}/state/source-index.json"))
package = index["packages"][package_id]
versions = package["versions"]
if not version_key:
    plan = json.load(open(f"{root}/state/promotion-plan.json"))
    matches = [row["versionKey"] for row in plan["packages"] if row["packageId"] == package_id and row.get("proposed")]
    if len(matches) != 1:
        sys.exit(f"pass a version key; proposed matches: {matches}")
    version_key = matches[0]
meta = versions[version_key]
out = {
    "packageId": package_id,
    "versionKey": version_key,
    "title": meta.get("title"),
    "categories": package.get("categories"),
    "packageRepo": meta.get("packageRepo"),
    "upstreamRepo": meta.get("upstreamRepo"),
    "s9pks": [],
}
for entry in meta.get("s9pks") or []:
    req, art = entry
    out["s9pks"].append({
        "requirements": {k: req.get(k) for k in ("device", "ram", "arch")},
        "urls": art.get("urls"),
        "commitment": art.get("commitment"),
        "publishedAt": art.get("publishedAt"),
    })
json.dump(out, sys.stdout, indent=2)
print()
PY
