from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

from eve_cli.main import build_parser, main


def write_manifest(path: Path, manifest: dict[str, Any]) -> None:
    path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")


def read_manifest(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def manifest_words(
    manifest: dict[str, Any], segment_index: int = 0
) -> list[dict[str, Any]]:
    segments = cast(list[dict[str, Any]], manifest["segments"])
    return cast(list[dict[str, Any]], segments[segment_index]["words"])


def test_tag_fillers_parser_registers_defaults() -> None:
    parser = build_parser()
    args = parser.parse_args(["tag-fillers", "edit.tjm.json"])

    assert args.command == "tag-fillers"
    assert args.manifest == "edit.tjm.json"
    assert args.input_manifest is None
    assert args.output is None
    assert args.filler is None
    assert args.json is False


def test_tag_fillers_parser_accepts_input_flag() -> None:
    parser = build_parser()
    args = parser.parse_args(["tag-fillers", "--input", "edit.tjm.json"])

    assert args.command == "tag-fillers"
    assert args.manifest is None
    assert args.input_manifest == "edit.tjm.json"
    assert args.output is None
    assert args.filler is None
    assert args.json is False


def test_tag_fillers_in_place_tags_matching_words(tmp_path: Path) -> None:
    manifest_path = tmp_path / "edit.tjm.json"
    manifest: dict[str, Any] = {
        "version": 1,
        "segments": [
            {
                "id": "seg-001",
                "start": 0.0,
                "end": 1.0,
                "text": "um hello",
                "words": [
                    {"token": "um", "start": 0.0, "end": 0.1},
                    {"token": "hello", "start": 0.1, "end": 0.5},
                ],
            }
        ],
    }
    write_manifest(manifest_path, manifest)

    exit_code = main(["tag-fillers", str(manifest_path)])
    updated = read_manifest(manifest_path)
    segments = cast(list[dict[str, Any]], updated["segments"])
    words = manifest_words(updated)

    assert exit_code == 0
    assert segments[0]["start"] == 0.0
    assert segments[0]["end"] == 1.0
    assert segments[0]["text"] == "um hello"
    assert words[0]["kind"] == "filler"
    assert "kind" not in words[1]


def test_tag_fillers_writes_to_alternate_output_path(tmp_path: Path) -> None:
    manifest_path = tmp_path / "edit.tjm.json"
    output_path = tmp_path / "tagged" / "edit.tjm.json"
    manifest: dict[str, Any] = {
        "version": 1,
        "segments": [
            {
                "id": "seg-001",
                "words": [
                    {"token": "ignored", "spoken": "Erm", "start": 0.0, "end": 0.1}
                ],
            }
        ],
    }
    write_manifest(manifest_path, manifest)

    exit_code = main(
        [
            "tag-fillers",
            "--input",
            str(manifest_path),
            "--output",
            str(output_path),
            "--filler",
            "erm",
        ]
    )

    assert exit_code == 0
    assert read_manifest(manifest_path) == manifest
    assert manifest_words(read_manifest(output_path))[0]["kind"] == "filler"


def test_tag_fillers_preserves_non_filler_words_and_unknown_keys(
    tmp_path: Path,
) -> None:
    manifest_path = tmp_path / "edit.tjm.json"
    manifest: dict[str, Any] = {
        "version": 1,
        "segments": [
            {
                "id": "seg-001",
                "words": [
                    {
                        "token": "hello",
                        "kind": "keyword",
                        "confidence": 0.91,
                        "custom": {"speaker": "A"},
                    },
                    {"token": "uh", "start": 0.2, "end": 0.3, "confidence": 0.42},
                ],
            }
        ],
    }
    write_manifest(manifest_path, manifest)

    exit_code = main(["tag-fillers", str(manifest_path)])
    updated = read_manifest(manifest_path)
    original_words = manifest_words(manifest)
    updated_words = manifest_words(updated)

    assert exit_code == 0
    assert updated_words[0] == original_words[0]
    assert updated_words[1] == {
        "token": "uh",
        "start": 0.2,
        "end": 0.3,
        "confidence": 0.42,
        "kind": "filler",
    }
