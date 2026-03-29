from __future__ import annotations

from argparse import ArgumentParser, Namespace

from eve_cli.output import emit_failure


def add_json_flag(parser: ArgumentParser) -> None:
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON output.",
    )


def not_implemented(args: Namespace, *, detail: str) -> int:
    emit_failure(args, detail, exit_code=2)
    return 2
