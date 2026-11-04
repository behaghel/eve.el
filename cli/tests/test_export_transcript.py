"""Source-clock exports must synchronize with unchanged original recordings."""

import json
from pathlib import Path

import pytest

from eve_cli.main import main


def manifest() -> dict:
    return {
        "version": "1.1",
        "sources": [
            {
                "id": "audio",
                "file": "original.m4a",
                "timebase": {"numerator": 1, "denominator": 48000},
            }
        ],
        "render": {"filler_policy": "keep", "preserve_short_gaps": 0},
        "segments": [
            {
                "id": "later",
                "source": "audio",
                "start_tick": 480000,
                "end_tick": 576000,
                "display_text": "La prière & <le silence>.",
            },
            {
                "id": "first",
                "source": "audio",
                "start_tick": 96000,
                "end_tick": 192000,
                "words": [
                    {
                        "spoken": "Jesus",
                        "display": "Jésus",
                        "start_tick": 96000,
                        "end_tick": 192000,
                    }
                ],
            },
        ],
    }


def export(tmp_path: Path, data: dict, *flags: str) -> tuple[int, Path]:
    source = tmp_path / "teaching.tjm.json"
    source.write_text(json.dumps(data))
    output = tmp_path / "teaching.vtt"
    return main(
        ["export-transcript", str(source), "--output", str(output), *flags]
    ), output


def test_source_export_preserves_leading_offset_gaps_and_source_order(tmp_path):
    code, output = export(tmp_path, manifest())
    assert code == 0
    assert output.read_text() == (
        "WEBVTT\n\n1\n00:00:02.000 --> 00:00:04.000\nJésus\n\n"
        "2\n00:00:10.000 --> 00:00:12.000\n"
        "La prière &amp; &lt;le silence&gt;.\n\n"
    )


def test_plain_text_export_needs_neither_media_nor_inference(tmp_path):
    code, output = export(tmp_path, manifest(), "--format", "text")
    assert code == 0
    assert output.read_text() == "Jésus\n\nLa prière & <le silence>.\n"
    assert not (tmp_path / "original.m4a").exists()


def test_multiple_sources_require_an_explicit_selection(tmp_path):
    data = manifest()
    data["sources"].append(
        {
            "id": "other",
            "file": "other.m4a",
            "timebase": {"numerator": 1, "denominator": 1000},
        }
    )
    code, output = export(tmp_path, data)
    assert code == 1
    assert not output.exists()
    code, output = export(tmp_path, data, "--source", "audio")
    assert code == 0
    assert "Jésus" in output.read_text()


@pytest.mark.parametrize(
    "mutation",
    [
        "overlap",
        "deleted",
        "cuts",
        "word-deleted",
        "drop-fillers",
        "collapsed",
        "missing-text",
    ],
)
def test_unsupported_or_invalid_exports_preserve_previous_output(tmp_path, mutation):
    data = manifest()
    if mutation == "overlap":
        data["segments"][0]["start_tick"] = 144000
    elif mutation == "deleted":
        data["segments"][0]["edit"] = {"deleted": True}
    elif mutation == "cuts":
        data["segments"][0]["edit"] = {
            "cuts": [{"start_tick": 480000, "end_tick": 490000}]
        }
    elif mutation == "word-deleted":
        data["segments"][1]["words"][0]["edit"] = {"deleted": True}
    elif mutation == "drop-fillers":
        data["render"]["filler_policy"] = "drop"
    elif mutation == "collapsed":
        data["segments"][0]["end_tick"] = 480001
    else:
        del data["segments"][0]["display_text"]
    output = tmp_path / "teaching.vtt"
    output.write_text("previous output")
    code, _ = export(tmp_path, data)
    assert code == 1
    assert output.read_text() == "previous output"


def test_output_cannot_overwrite_manifest_or_original_media(tmp_path):
    source = tmp_path / "teaching.tjm.json"
    source.write_text(json.dumps(manifest()))
    before = source.read_bytes()
    media = tmp_path / "original.m4a"
    media.write_bytes(b"original audio")
    for output in [source, media]:
        assert main(["export-transcript", str(source), "--output", str(output)]) == 1
    assert source.read_bytes() == before
    assert media.read_bytes() == b"original audio"


def test_unknown_source_and_invalid_json_are_readable_errors(tmp_path, capsys):
    code, _ = export(tmp_path, manifest(), "--source", "missing")
    assert code == 1
    source = tmp_path / "bad.json"
    source.write_text("not JSON")
    assert (
        main(["export-transcript", str(source), "--output", str(tmp_path / "out.vtt")])
        == 1
    )
    assert "Traceback" not in capsys.readouterr().err


@pytest.mark.parametrize("field", ["display_text", "spoken_text"])
def test_invalid_text_is_reported_without_replacing_output(tmp_path, field):
    data = manifest()
    data["segments"][0].pop("display_text")
    data["segments"][0][field] = {"invalid": "structured text"}
    output = tmp_path / "teaching.vtt"
    output.write_text("previous output")
    code, _ = export(tmp_path, data)
    assert code == 1
    assert output.read_text() == "previous output"


def test_exact_source_ticks_are_rounded_once_with_ties_to_even(tmp_path):
    data = manifest()
    data["sources"][0]["timebase"]["denominator"] = 2000
    data["segments"] = [
        {
            "id": "cue",
            "source": "audio",
            "start_tick": 1,
            "end_tick": 5,
            "display_text": "Bonjour.",
        }
    ]
    code, output = export(tmp_path, data)
    assert code == 0
    assert "00:00:00.000 --> 00:00:00.002" in output.read_text()
