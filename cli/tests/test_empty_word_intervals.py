"""Empty transcript tokens have text, but no source-media extent."""

import json
import shutil
import subprocess
from copy import deepcopy
from fractions import Fraction
from pathlib import Path

import pytest

from eve_cli import tjm_v11
from eve_cli.commands import text_edit, transcribe
from eve_cli.main import main


def manifest() -> dict:
    return {
        "version": "1.1",
        "sources": [
            {
                "id": "s",
                "file": "source.wav",
                "timebase": {"numerator": 1, "denominator": 48000},
            }
        ],
        "render": {"filler_policy": "keep", "preserve_short_gaps": 0},
        "segments": [
            {
                "id": "sentence",
                "source": "s",
                "start_tick": 0,
                "end_tick": 96000,
                "spoken_text": "alpha empty beta",
                "display_text": "alpha empty beta",
                "words": [
                    {"spoken": "alpha", "start_tick": 0, "end_tick": 24000},
                    {"spoken": "empty", "start_tick": 24000, "end_tick": 24000},
                    {"spoken": "beta", "start_tick": 48000, "end_tick": 96000},
                ],
            }
        ],
    }


@pytest.mark.parametrize("point", [0, 24000, 96000])
def test_empty_word_is_valid_at_any_in_bounds_point(point):
    data = manifest()
    word = data["segments"][0]["words"][1]
    word.update(start_tick=point, end_tick=point)
    assert tjm_v11.parse_manifest(data) == data


@pytest.mark.parametrize("start,end", [(24000, 23999), (-1, -1), (96001, 96001)])
def test_empty_word_support_does_not_admit_malformed_bounds(start, end):
    data = manifest()
    data["segments"][0]["words"][1].update(start_tick=start, end_tick=end)
    with pytest.raises(tjm_v11.ValidationError):
        tjm_v11.parse_manifest(data)


def test_empty_media_segment_remains_invalid():
    data = manifest()
    data["segments"][0].update(start_tick=0, end_tick=0)
    with pytest.raises(tjm_v11.ValidationError):
        tjm_v11.parse_manifest(data)


@pytest.mark.parametrize("all_empty", [False, True])
@pytest.mark.parametrize("operation", ["keep", "delete", "drop-filler"])
def test_empty_word_edits_change_text_not_media_coverage(
    tmp_path, all_empty, operation
):
    data = manifest()
    segment = data["segments"][0]
    if all_empty:
        segment["words"] = [segment["words"][1]]
        segment["spoken_text"] = segment["display_text"] = "empty"
    word = next(w for w in segment["words"] if w["spoken"] == "empty")
    if operation == "delete":
        word["edit"] = {"deleted": True}
    elif operation == "drop-filler":
        word["edit"] = {"filler": True}
        data["render"]["filler_policy"] = "drop"
    original = deepcopy(data)
    prepared = text_edit.prepare_render_manifest(data, tmp_path)
    assert text_edit._subtitle_ranges(prepared["segments"][0], Fraction(1, 48000)) == [
        (Fraction(0), Fraction(2))
    ]
    cues = text_edit.build_subtitle_cues(prepared)
    expected = (
        ("empty" if all_empty else "alpha empty beta")
        if operation == "keep"
        else ("" if all_empty else "alpha beta")
    )
    assert cues == ([(0, 2, expected)] if expected else [])
    if expected:
        caption = text_edit.build_token_captions(prepared)[0]
        assert " ".join(token.text for token in caption.tokens) == expected
        for token in caption.tokens:
            if token.text == "empty":
                assert token.intervals == []
    assert data == original


def test_empty_word_semantics_do_not_reuse_old_timeline_caches(tmp_path, monkeypatch):
    source = tmp_path / "source.mp4"
    source.touch()
    item = {
        "type": "segment",
        "source_path": source,
        "segment": manifest()["segments"][0],
        "key": None,
    }
    current = text_edit.segment_cache_key(item, "draft")
    monkeypatch.setattr(text_edit, "TIMELINE_RENDER_VERSION", "native-frame-clock-v2")
    assert text_edit.segment_cache_key(item, "draft") != current


def test_positive_word_deletion_still_cuts_media(tmp_path):
    data = manifest()
    data["segments"][0]["words"][0]["edit"] = {"deleted": True}
    prepared = text_edit.prepare_render_manifest(data, tmp_path)
    assert text_edit._subtitle_ranges(prepared["segments"][0], Fraction(1, 48000)) == [
        (Fraction(1), Fraction(2))
    ]
    assert text_edit.build_subtitle_cues(prepared) == [(0, 1, "empty beta")]


@pytest.mark.parametrize("all_empty", [False, True])
@pytest.mark.parametrize("interval", [(0.5, 0.5), (0.500001, 0.500002)])
def test_default_transcription_preserves_empty_words_and_segment_envelope(
    tmp_path, monkeypatch, all_empty, interval
):
    source = tmp_path / "source.wav"
    source.touch()
    monkeypatch.setattr(transcribe, "probe_audio_sample_rate", lambda _: 48000)
    words = [{"token": "empty", "start": interval[0], "end": interval[1]}]
    if not all_empty:
        words += [{"token": "beta", "start": 0.5, "end": 2.0}]
    monkeypatch.setattr(
        transcribe,
        "transcribe_faster_whisper",
        lambda *args: [
            {
                "id": "raw",
                "source": "clip01",
                "start": 0,
                "end": 2,
                "text": " ".join(w["token"] for w in words),
                "words": words,
            }
        ],
    )
    output = tmp_path / "result.tjm.json"
    assert main(["transcribe", str(source), "--output", str(output)]) == 0
    result = tjm_v11.parse_manifest(json.loads(output.read_text()))
    actual = [w for s in result["segments"] for w in s["words"]]
    assert [(w["spoken"], w["start_tick"], w["end_tick"]) for w in actual] == [
        (w["token"], round(w["start"] * 48000), round(w["end"] * 48000)) for w in words
    ]
    assert result["segments"][0]["start_tick"] == 0
    assert result["segments"][-1]["end_tick"] == 96000


@pytest.mark.media
@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="FFmpeg unavailable")
@pytest.mark.parametrize("video", [False, True])
@pytest.mark.parametrize("all_empty", [False, True])
def test_native_export_preserves_media_and_empty_word_caption_edits(
    tmp_path: Path, video: bool, all_empty: bool
):
    source = tmp_path / ("source.mp4" if video else "source.wav")
    command = [
        "ffmpeg",
        "-v",
        "error",
        "-f",
        "lavfi",
        "-i",
        "sine=frequency=440:sample_rate=48000:duration=2",
    ]
    if video:
        command += [
            "-f",
            "lavfi",
            "-i",
            "testsrc2=s=160x90:r=25:d=2",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
        ]
    command += [str(source)]
    subprocess.run(command, check=True, capture_output=True)
    data = manifest()
    data["sources"][0]["file"] = source.name
    if all_empty:
        data["segments"][0]["words"] = [data["segments"][0]["words"][1]]
        data["segments"][0]["spoken_text"] = "empty"
        data["segments"][0]["display_text"] = "empty"
    path = tmp_path / "edit.tjm.json"
    extension = ".mp4" if video else ".m4a"
    outputs = []
    for deleted in (False, True):
        data["segments"][0]["words"][0 if all_empty else 1]["edit"] = {
            "deleted": deleted
        }
        path.write_text(json.dumps(data))
        output = tmp_path / (f"out-{deleted}" + extension)
        assert (
            main(["text-edit", str(path), "--output", str(output), "--subtitles"]) == 0
        )
        caption = output.with_suffix(".vtt").read_text()
        assert ("empty" in caption) is not deleted
        raw = subprocess.check_output(
            [
                "ffmpeg",
                "-v",
                "error",
                "-i",
                str(output),
                "-map",
                "0:a:0",
                "-f",
                "s16le",
                "-c:a",
                "pcm_s16le",
                "-",
            ]
        )
        assert 1.99 <= len(raw) / (48000 * 2) <= 2.03
        frames = b""
        if video:
            frames = subprocess.check_output(
                [
                    "ffmpeg",
                    "-v",
                    "error",
                    "-i",
                    str(output),
                    "-map",
                    "0:v:0",
                    "-pix_fmt",
                    "rgb24",
                    "-f",
                    "rawvideo",
                    "-",
                ]
            )
            assert len(frames) == 160 * 90 * 3 * 50
        outputs.append((raw, frames))
    assert outputs[0] == outputs[1]
