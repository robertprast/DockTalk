#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

: "${TOGETHER_API_KEY:?set TOGETHER_API_KEY first}"

python3 agent.py --smoke
