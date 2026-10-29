from __future__ import annotations

import json
import os
import tempfile
from argparse import ArgumentParser, Namespace
from pathlib import Path
from typing import Any

from eve_cli.output import emit_failure


def add_json_flag(parser: ArgumentParser) -> None:
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON output.",
    )


def write_json_atomic(path: Path, data: dict[str, Any], *, pretty: bool = True) -> None:
    """Publish complete JSON in the destination filesystem, cleaning up on failure."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, delete=False
        ) as stream:
            temporary = Path(stream.name)
            json.dump(
                data,
                stream,
                indent=2 if pretty else None,
                separators=None if pretty else (",", ":"),
                ensure_ascii=False,
                allow_nan=False,
            )
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def not_implemented(args: Namespace, *, detail: str) -> int:
    emit_failure(args, detail, exit_code=2)
    return 2
