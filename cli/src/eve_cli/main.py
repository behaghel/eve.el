from __future__ import annotations

import argparse
import importlib
import json
import sys
from collections.abc import Sequence

from eve_cli import __version__, subtitles, tjm_v11
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
    try:
        return int(handler(args))
    except (subtitles.SubtitleError, tjm_v11.ValidationError, OSError) as error:
        if getattr(args, "json", False):
            payload = (
                {"valid": False, "errors": [{"message": str(error)}], "warnings": []}
                if getattr(args, "validate", False)
                else {"error": str(error)}
            )
            print(json.dumps(payload))
        else:
            print(f"[eve {args.command}] ERROR: {error}", file=sys.stderr)
        return 1
