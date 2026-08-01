from __future__ import annotations

import json
from argparse import ArgumentParser, Namespace, _SubParsersAction
from pathlib import Path
from typing import Any

from ..utils.fillers import (
    DEFAULT_FILLERS,
    build_filler_phrases,
    build_filler_set,
    is_filler,
    normalise_filler_phrase,
    phrase_matches,
)
from .common import add_json_flag


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
        help="Additional filler word or phrase to tag (repeatable)",
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


def _effective_word_kind(word: dict[str, Any]) -> Any:
    edit = word.get("edit")
    if isinstance(edit, dict) and "kind" in edit:
        return edit["kind"]
    return word.get("kind")


def _mark_word_filler(word: dict[str, Any]) -> bool:
    """Mark WORD as filler in canonical edit metadata.

    Legacy top-level ``kind`` is preserved as compatibility input, but new
    filler classification is written to ``word.edit.kind``.
    """
    changed = _effective_word_kind(word) != "filler"
    edit = word.get("edit")
    if not isinstance(edit, dict):
        edit = {}
        word["edit"] = edit
    edit["kind"] = "filler"
    return bool(changed)


def tag_manifest_fillers(
    manifest: dict[str, Any], fillers: list[str] | tuple[str, ...] | frozenset[str]
) -> int:
    tagged_words = 0
    phrases = build_filler_phrases(fillers)
    single_fillers = build_filler_set(
        phrase[0] for phrase in phrases if len(phrase) == 1
    )
    multi_phrases = tuple(phrase for phrase in phrases if len(phrase) > 1)
    segments = manifest.get("segments")
    if not isinstance(segments, list):
        return tagged_words

    for segment in segments:
        if not isinstance(segment, dict):
            continue
        words = segment.get("words")
        if not isinstance(words, list):
            continue
        normalised_words = []
        for word in words:
            word_phrase = (
                normalise_filler_phrase(word_text(word))
                if isinstance(word, dict)
                else ()
            )
            normalised_words.append(word_phrase[0] if word_phrase else "")
        index = 0
        while index < len(words):
            word = words[index]
            if not isinstance(word, dict):
                index += 1
                continue
            match_length = 0
            for phrase in multi_phrases:
                if phrase_matches(normalised_words, phrase, index):
                    match_length = len(phrase)
                    break
            if match_length:
                for phrase_word in words[index : index + match_length]:
                    if isinstance(phrase_word, dict) and _mark_word_filler(phrase_word):
                        tagged_words += 1
                index += match_length
                continue
            if is_filler(word_text(word), single_fillers):
                if _mark_word_filler(word):
                    tagged_words += 1
            index += 1
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
    fillers = [*DEFAULT_FILLERS, *(args.filler or [])]

    manifest = load_manifest(manifest_path)
    tag_manifest_fillers(manifest, fillers)
    write_manifest(manifest, output_path)
    return 0
