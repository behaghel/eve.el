#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "$0")/.." && pwd)"

"$repo_root/scripts/startup-health-check.sh"

cd "$repo_root/cli"
uv run ruff format --check src tests
uv run ruff check src tests
uv run mypy src
uv run python -m compileall src tests
# Coverage threshold: 85% accounts for rendering code paths (cache I/O,
# checkpoint, progressive output) that require ffmpeg at runtime and are
# covered by the end-to-end test only when ffmpeg is available.
uv run pytest --cov=eve_cli --cov-report=term-missing --cov-fail-under=85
uv build
