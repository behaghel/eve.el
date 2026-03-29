#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "$0")/.." && pwd)"

test -f "$repo_root/devenv.nix"
test -f "$repo_root/eve.el"
test -f "$repo_root/cli/pyproject.toml"

cd "$repo_root/cli"
uv sync --all-extras --group dev
uv run eve doctor --json >/dev/null
