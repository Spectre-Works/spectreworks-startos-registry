#!/bin/bash
set -euo pipefail
registry=${1:-https://startos-registry.spectreworks.io}
start-cli --registry "${registry}" registry info --format json
start-cli --registry "${registry}" registry package index --format json
start-cli --registry "${registry}" registry admin list --format json
