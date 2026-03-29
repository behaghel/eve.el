#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/../cli"
uv build
