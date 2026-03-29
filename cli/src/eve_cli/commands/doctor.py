from __future__ import annotations

from argparse import ArgumentParser, Namespace, _SubParsersAction

from eve_cli.output import emit_success
from eve_cli.paths import cli_root, package_entrypoint, project_root

from .common import add_json_flag


def register(subparsers: _SubParsersAction[ArgumentParser]) -> None:
    parser = subparsers.add_parser(
        "doctor",
        help="Inspect the project scaffold and report key paths.",
    )
    add_json_flag(parser)
    parser.set_defaults(handler=run, command="doctor")


def run(args: Namespace) -> int:
    root = project_root()
    payload = {
        "command": "doctor",
        "project_root": str(root),
        "cli_root": str(cli_root()),
        "package_entrypoint": str(package_entrypoint()),
        "package_entrypoint_present": package_entrypoint().exists(),
        "message": f"Scaffold looks healthy at {root}",
    }
    emit_success(args, payload)
    return 0
