#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "$0")/.." && pwd)"

"$repo_root/scripts/startup-health-check.sh"

cd "$repo_root/cli"
uv run ruff format --check src tests
uv run ruff check src tests
uv run mypy src
uv run python -m compileall src tests
# The full health suite must exercise real media paths, not pass by skipping
# them. Bare pytest still permits optional developer/platform-specific skips.
uv run pytest --require-media -rs --cov=eve_cli --cov-report=term-missing --cov-fail-under=85
uv build
