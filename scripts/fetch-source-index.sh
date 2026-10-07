#!/bin/bash
set -euo pipefail
root=$(cd "$(dirname "$0")/.." && pwd)
start-cli --registry https://start9.mempool.guide registry package index --format json \
  > "${root}/state/source-index.json"
echo "wrote ${root}/state/source-index.json"
