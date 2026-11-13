"""Audio-only rendering uses real samples and the shared TJM subtitle clock."""

from __future__ import annotations

import json
import shutil
import struct
import subprocess
import wave
from pathlib import Path
from typing import Any

import pytest

from eve_cli.commands import audio_render, text_edit
from eve_cli.main import main

pytestmark = [
    pytest.mark.media,
    pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="FFmpeg unavailable"),
]
RATE = 48000


@pytest.fixture
def audio_edit(tmp_path: Path) -> tuple[Path, dict[str, Any], tuple[int, ...]]:
    samples = tuple((i * 73) % 20000 - 10000 for i in range(RATE * 2))
    source = tmp_path / "original audio.wav"
    with wave.open(str(source), "wb") as f:
        f.setparams((1, 2, RATE, 0, "NONE", "not compressed"))
        f.writeframes(struct.pack(f"<{len(samples)}h", *samples))

    def word(text: str, start: int, end: int, **edit: Any) -> dict[str, Any]:
        return dict(
            spoken=text, display=text, start_tick=start, end_tick=end, edit=edit
        )

    data = {
        "version": "1.1",
        "sources": [
            dict(id="a", file=source.name, timebase=dict(numerator=1, denominator=RATE))
        ],
        "render": dict(filler_policy="drop", preserve_short_gaps=0.25),
        "segments": [
            dict(
                id="first",
                source="a",
                start_tick=4800,
                end_tick=62400,
                start=999,
                end=1000,
                display_text="alpha euh beta",
                words=[
                    word("alpha", 4800, 24000),
                    word("euh", 24000, 43200, filler=True),
                    word("beta", 43200, 62400),
                ],
            ),
            dict(
                id="second",
                source="a",
                start_tick=72000,
                end_tick=86400,
                display_text="gamma",
            ),
        ],
    }
    manifest = tmp_path / "edit.tjm.json"
    manifest.write_text(json.dumps(data))
    return manifest, data, samples


def pcm(path: Path) -> tuple[int, ...]:
    raw = subprocess.check_output(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            str(path),
            "-map",
            "0:a:0",
            "-c:a",
            "pcm_s16le",
            "-f",
            "s16le",
            "-",
        ]
    )
    return struct.unpack(f"<{len(raw) // 2}h", raw)


def test_audio_preserves_samples_cuts_gaps_and_subtitle_clock(
    audio_edit: tuple[Path, dict[str, Any], tuple[int, ...]],
) -> None:
    path, data, source = audio_edit
    before = path.read_bytes()
    prepared = text_edit.prepare_render_manifest(data, path.parent)
    intervals = audio_render.plan(prepared)
    inputs = {item.source: audio_render.probe(item.source) for item in intervals}
    working = path.parent / "intermediates"
    working.mkdir()
    pieces = audio_render.render_pieces(intervals, inputs, working)
    actual = tuple(sample for piece in pieces for sample in pcm(piece))
    output = path.parent / "edited.m4a"
    assert main(["text-edit", str(path), "--output", str(output), "--subtitles"]) == 0
    assert len(actual) == 62400  # .4 + .4 + preserved .2 + .3 seconds
    assert actual[600:18600] == source[5400:23400]
    assert actual[19800:-600] == source[43800:85800]
    # No added fades at caption boundaries or within the preserved gap.
    assert actual[38000:50000] == source[62000:74000]
    assert path.read_bytes() == before
    vtt = output.with_suffix(".vtt").read_text()
    assert "00:00:00.000 --> 00:00:00.800" in vtt
    assert "00:00:01.000 --> 00:00:01.300" in vtt
    assert "alpha beta" in vtt and "euh" not in vtt and "gamma" in vtt
    streams = json.loads(
        subprocess.check_output(
            ["ffprobe", "-v", "error", "-show_streams", "-of", "json", str(output)]
        )
    )["streams"]
    assert [s["codec_type"] for s in streams] == ["audio"]


def test_audio_m4a_has_one_audio_stream_and_matching_sidecar(
    audio_edit: tuple[Path, dict[str, Any], tuple[int, ...]],
) -> None:
    path, _, _ = audio_edit
    output = path.parent / "edited.m4a"
    assert main(["text-edit", str(path), "--output", str(output), "--subtitles"]) == 0
    streams = json.loads(
        subprocess.check_output(
            ["ffprobe", "-v", "error", "-show_streams", "-of", "json", str(output)]
        )
    )["streams"]
    assert [(s["codec_type"], s["codec_name"]) for s in streams] == [("audio", "aac")]
    assert abs(float(streams[0]["duration"]) - 1.3) < 1 / RATE
    assert output.with_suffix(".vtt").is_file()


@pytest.mark.parametrize("operation", ["--validate", "--dry-run"])
def test_audio_preflight_is_structured_and_writes_no_output(
    audio_edit, operation, capsys
):
    path, _, _ = audio_edit
    output = path.parent / "edited.m4a"
    assert (
        main(["text-edit", str(path), "--output", str(output), operation, "--json"])
        == 0
    )
    result = json.loads(capsys.readouterr().out)
    if operation == "--validate":
        assert result == dict(valid=True, errors=[], warnings=[])
    else:
        assert result["changed_segments"] == 2 and result["cached_segments"] == 0
    assert not output.exists() and not (path.parent / ".eve-cache").exists()


@pytest.mark.parametrize(
    "change,message",
    [
        ("empty", "No retained audio"),
        ("broll", "does not support b-roll"),
        ("bounds", "exceeds decoded source"),
        ("grid", "sample grid"),
    ],
)
def test_audio_rejects_unsupported_edits_without_replacing_output(
    audio_edit, change, message, capsys
):
    path, data, _ = audio_edit
    if change == "empty":
        for segment in data["segments"]:
            segment["edit"] = dict(deleted=True)
    elif change == "broll":
        data["segments"][0]["edit"] = dict(
            broll=dict(file="unused.mp4", mode="replace", audio="source")
        )
    elif change == "bounds":
        data["segments"][1]["end_tick"] = RATE * 3
    else:
        data["sources"][0]["timebase"]["denominator"] = RATE * 2
        data["segments"][0]["start_tick"] = 4801
        data["segments"][0]["words"][0]["start_tick"] = 4801
    path.write_text(json.dumps(data))
    before = path.read_bytes()
    output = path.parent / "edited.m4a"
    output.write_bytes(b"previous output")
    assert main(["text-edit", str(path), "--output", str(output)]) == 1
    assert message in capsys.readouterr().err
    assert output.read_bytes() == b"previous output" and path.read_bytes() == before


@pytest.mark.parametrize("flag", ["--subtitles", "--pretty-manifest", "--report"])
def test_audio_outputs_cannot_overwrite_the_input_manifest(audio_edit, flag, capsys):
    path, _, _ = audio_edit
    before = path.read_bytes()
    assert (
        main(
            [
                "text-edit",
                str(path),
                "--output",
                str(path.parent / "out.m4a"),
                flag,
                str(path),
            ]
        )
        == 1
    )
    assert "overwrite" in capsys.readouterr().err
    assert path.read_bytes() == before


def test_failed_audio_encoder_preserves_previous_media_and_captions(
    audio_edit, monkeypatch
):
    path, _, _ = audio_edit
    output = path.parent / "out.m4a"
    output.write_bytes(b"previous media")
    vtt = output.with_suffix(".vtt")
    vtt.write_text("previous captions")
    original = text_edit.run_ffmpeg

    def fail_encoding(command, *, context=None):
        if context == "encoding assembled audio":
            raise RuntimeError("encoder failed")
        return original(command, context=context)

    monkeypatch.setattr(text_edit, "run_ffmpeg", fail_encoding)
    with pytest.raises(RuntimeError, match="encoder failed"):
        main(["text-edit", str(path), "--output", str(output), "--subtitles"])
    assert output.read_bytes() == b"previous media"
    assert vtt.read_text() == "previous captions"


def test_audio_many_short_cuts_do_not_accumulate_aac_padding(audio_edit):
    path, data, _ = audio_edit
    data["segments"] = [
        dict(
            id=f"s{i}",
            source="a",
            start_tick=i * 700,
            end_tick=i * 700 + 443,
            display_text=f"word{i}",
        )
        for i in range(24)
    ]
    data["render"]["preserve_short_gaps"] = 0
    path.write_text(json.dumps(data))
    output = path.parent / "many.m4a"
    report = path.parent / "report.json"
    assert (
        main(["text-edit", str(path), "--output", str(output), "--report", str(report)])
        == 0
    )
    duration = json.loads(
        subprocess.check_output(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "stream=duration_ts,time_base",
                "-of",
                "json",
                str(output),
            ]
        )
    )["streams"][0]
    assert duration["duration_ts"] == 24 * 443
    assert duration["time_base"] == "1/48000"
    assert json.loads(report.read_text())["samples"] == 24 * 443


def test_protected_filler_remains_audible_and_in_subtitles(audio_edit):
    path, data, _ = audio_edit
    data["segments"][0]["words"][1]["edit"]["filler"] = "no-filler"
    path.write_text(json.dumps(data))
    output = path.parent / "protected.m4a"
    report = path.parent / "report.json"
    assert (
        main(
            [
                "text-edit",
                str(path),
                "--output",
                str(output),
                "--subtitles",
                "--report",
                str(report),
            ]
        )
        == 0
    )
    assert "alpha euh beta" in output.with_suffix(".vtt").read_text()
    assert json.loads(report.read_text())["samples"] == 81600


def test_audio_source_presentation_offset_is_not_added_to_the_cut(audio_edit):
    path, data, source_samples = audio_edit
    shifted = path.parent / "shifted.m4a"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            str(path.parent / data["sources"][0]["file"]),
            "-c:a",
            "alac",
            "-output_ts_offset",
            "1.25",
            str(shifted),
        ],
        check=True,
    )
    info = audio_render.probe(shifted)
    assert info.origin == 1.25
    data["sources"][0]["file"] = shifted.name
    for segment in data["segments"]:
        for item in [segment, *segment.get("words", [])]:
            item["start_tick"] += 60000
            item["end_tick"] += 60000
    path.write_text(json.dumps(data))
    prepared = text_edit.prepare_render_manifest(data, path.parent)
    intervals = audio_render.plan(prepared)
    work = path.parent / "offset-pieces"
    work.mkdir()
    pieces = audio_render.render_pieces(intervals, {shifted: info}, work)
    actual = tuple(sample for part in pieces for sample in pcm(part))
    assert len(actual) == 62400
    assert actual[600:18600] == source_samples[5400:23400]
    output = path.parent / "offset.m4a"
    assert main(["text-edit", str(path), "--output", str(output), "--subtitles"]) == 0
    assert "00:00:00.000 --> 00:00:00.800" in output.with_suffix(".vtt").read_text()


def test_audio_mismatched_source_formats_fail_without_resampling(audio_edit, capsys):
    path, data, _ = audio_edit
    other = path.parent / "other.wav"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            str(path.parent / data["sources"][0]["file"]),
            "-ar",
            "16000",
            str(other),
        ],
        check=True,
    )
    data["sources"].append(
        dict(id="b", file=other.name, timebase=dict(numerator=1, denominator=16000))
    )
    data["segments"][1].update(source="b", start_tick=24000, end_tick=28800)
    path.write_text(json.dumps(data))
    output = path.parent / "out.m4a"
    assert main(["text-edit", str(path), "--output", str(output)]) == 1
    assert "matching sample rates" in capsys.readouterr().err
    assert not output.exists()


def test_audio_burning_is_rejected_before_font_resolution(audio_edit, capsys):
    path, data, _ = audio_edit
    data["subtitles"] = dict(
        mode="burn",
        style="plain",
        font=dict(family="Missing Font", size=48, bold=True),
        placement=dict(anchor="bottom-center", bottom_margin=60),
        color="#FFFFFF",
        outline=dict(color="#000000", width=3),
    )
    path.write_text(json.dumps(data))
    output = path.parent / "out.m4a"
    assert (
        main(["text-edit", str(path), "--output", str(output), "--validate", "--json"])
        == 1
    )
    result = json.loads(capsys.readouterr().out)
    assert result["valid"] is False and "cannot burn" in result["errors"][0]["message"]
    assert not output.exists()


def test_known_layout_changes_captions_without_changing_audio(audio_edit):
    path, data, _ = audio_edit
    data["render"]["filler_policy"] = "keep"
    segment = data["segments"][0]
    segment.pop("display_text")
    for word, text in zip(segment["words"], ["well", "-", "being"], strict=True):
        word.update(spoken=text, display=text)
    results = []
    for known in (False, True):
        if known:
            for word in segment["words"]:
                word["separator_before"] = ""
        path.write_text(json.dumps(data))
        output = path.parent / f"layout-{known}.m4a"
        assert (
            main(["text-edit", str(path), "--output", str(output), "--subtitles"]) == 0
        )
        text = output.with_suffix(".vtt").read_text()
        assert ("well-being" if known else "well - being") in text
        results.append(output.read_bytes())
    assert results[0] == results[1]


def test_wav_is_not_a_public_export_format(audio_edit, capsys):
    path, _, _ = audio_edit
    output = path.parent / "out.wav"
    assert main(["text-edit", str(path), "--output", str(output)]) == 1
    assert "Choose M4A" in capsys.readouterr().err
    assert not output.exists()
