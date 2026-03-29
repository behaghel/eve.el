#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/../cli"
uv sync --all-extras --group dev "$@"
