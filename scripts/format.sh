#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/../cli"
uv run ruff format src tests
uv run ruff check --fix src tests
