#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/../cli"
uv run ruff format --check src tests
uv run ruff check src tests
uv run mypy src
