from __future__ import annotations

import argparse
import importlib
from collections.abc import Sequence

from eve_cli import __version__
from eve_cli.commands import batch, denoise, doctor, text_edit, transcribe, trim_fillers

tag_fillers = importlib.import_module("eve_cli.commands.tag_fillers")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="eve",
        description="Command layer for the eve.el project.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )

    subparsers = parser.add_subparsers(dest="command")
    doctor.register(subparsers)
    transcribe.register(subparsers)
    text_edit.register(subparsers)
    tag_fillers.register(subparsers)
    trim_fillers.register(subparsers)
    denoise.register(subparsers)
    batch.register(subparsers)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    handler = getattr(args, "handler", None)
    if handler is None:
        parser.print_help()
        return 1
    return int(handler(args))
