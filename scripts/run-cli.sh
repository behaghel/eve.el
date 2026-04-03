#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CLI_DIR="$SCRIPT_DIR/../cli"
VENV_DIR="$CLI_DIR/.venv"
EVE_BIN="$VENV_DIR/bin/eve"

# Fast path: venv already bootstrapped
if [[ -x "$EVE_BIN" ]]; then
    exec "$EVE_BIN" "$@"
fi

# Bootstrap: create venv and install from shipped wheel
echo "eve: bootstrapping CLI environment..." >&2
WHEEL=("$CLI_DIR"/dist/eve_cli-*.whl)
if [[ ! -f "${WHEEL[0]}" ]]; then
    echo "Error: no wheel found in cli/dist/. Run 'cd cli && uv build' first." >&2
    exit 1
fi

python3 -m venv "$VENV_DIR"
"$VENV_DIR/bin/pip" install --quiet "${WHEEL[0]}"
exec "$EVE_BIN" "$@"
