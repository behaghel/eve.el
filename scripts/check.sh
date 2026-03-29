#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "$0")/.." && pwd)"

"$repo_root/scripts/startup-health-check.sh"

cd "$repo_root/cli"
uv run ruff format --check src tests
uv run ruff check src tests
uv run mypy src
uv run python -m compileall src tests
uv run pytest --cov=eve_cli --cov-report=term-missing --cov-fail-under=90
uv build
