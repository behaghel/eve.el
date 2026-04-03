from __future__ import annotations

import json
from argparse import ArgumentParser, Namespace, _SubParsersAction
from pathlib import Path
from typing import Any

from .common import add_json_flag
from ..utils.fillers import DEFAULT_FILLERS, build_filler_set, is_filler


def register(subparsers: _SubParsersAction[ArgumentParser]) -> None:
    parser = subparsers.add_parser(
        "tag-fillers",
        help="Tag filler words in a TJM manifest without trimming media.",
    )
    parser.add_argument(
        "manifest",
        nargs="?",
        help="Path to the TJM JSON manifest (positional form)",
    )
    parser.add_argument(
        "--input",
        dest="input_manifest",
        help="Path to the TJM JSON manifest",
    )
    parser.add_argument(
        "--output",
        help="Optional output path; overwrites the input manifest when omitted",
    )
    parser.add_argument(
        "--filler",
        action="append",
        default=None,
        help="Additional filler word to tag (repeatable)",
    )
    add_json_flag(parser)
    parser.set_defaults(handler=run, command="tag-fillers", parser=parser)


def load_manifest(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as file_handle:
        data = json.load(file_handle)
    if not isinstance(data, dict):
        raise ValueError(f"Manifest '{path}' must decode to a JSON object")
    return data


def write_manifest(manifest: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file_handle:
        json.dump(manifest, file_handle, indent=2, ensure_ascii=False)


def word_text(word: dict[str, Any]) -> str:
    spoken = word.get("spoken")
    if isinstance(spoken, str) and spoken.strip():
        return spoken
    token = word.get("token")
    if isinstance(token, str):
        return token
    return ""


def tag_manifest_fillers(manifest: dict[str, Any], fillers: frozenset[str]) -> int:
    tagged_words = 0
    segments = manifest.get("segments")
    if not isinstance(segments, list):
        return tagged_words

    for segment in segments:
        if not isinstance(segment, dict):
            continue
        words = segment.get("words")
        if not isinstance(words, list):
            continue
        for word in words:
            if not isinstance(word, dict):
                continue
            if is_filler(word_text(word), fillers):
                if word.get("kind") != "filler":
                    tagged_words += 1
                word["kind"] = "filler"
    return tagged_words


def run(args: Namespace) -> int:
    parser = getattr(args, "parser", None)
    manifest_arg = args.input_manifest or args.manifest
    if manifest_arg is None:
        if parser is not None:
            parser.error("one of the arguments manifest --input is required")
        raise ValueError("tag-fillers requires a manifest path")

    manifest_path = Path(manifest_arg).expanduser()
    output_path = Path(args.output).expanduser() if args.output else manifest_path
    filler_set = build_filler_set([*DEFAULT_FILLERS, *(args.filler or [])])

    manifest = load_manifest(manifest_path)
    tag_manifest_fillers(manifest, filler_set)
    write_manifest(manifest, output_path)
    return 0
