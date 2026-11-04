"""Explicit segment timing never manufactures per-word timing."""

import json
from types import SimpleNamespace

import pytest

from eve_cli.commands import transcribe
from eve_cli.main import build_parser, main


def test_word_timestamps_remain_the_default():
    args = build_parser().parse_args(["transcribe", "in.m4a", "--output", "out.json"])
    assert args.timestamps == "words"


def test_segment_mode_exports_native_caption_without_timed_words(tmp_path, monkeypatch):
    media = tmp_path / "in.m4a"
    media.touch()
    output = tmp_path / "out.tjm.json"
    calls = []

    class Model:
        def __init__(self, *args, **kwargs):
            pass

        def transcribe(self, path, **kwargs):
            calls.append(kwargs)
            return [
                SimpleNamespace(
                    start=2.0, end=4.0, text=" Jésus nous appelle. ", words=None
                )
            ], None

    monkeypatch.setattr(transcribe, "_load_whisper_model", lambda: Model)
    monkeypatch.setattr(transcribe, "ffmpeg_extract", lambda *args: None)
    monkeypatch.setattr(transcribe, "probe_audio_sample_rate", lambda _: 48000)
    assert (
        main(
            [
                "transcribe",
                str(media),
                "--output",
                str(output),
                "--language",
                "fr",
                "--timestamps",
                "segments",
            ]
        )
        == 0
    )
    assert calls[0]["word_timestamps"] is False
    data = json.loads(output.read_text())
    segment = data["segments"][0]
    assert segment["start_tick"] == 96000
    assert segment["end_tick"] == 192000
    assert segment["spoken_text"] == "Jésus nous appelle."
    assert segment["display_text"] == segment["spoken_text"]
    assert "words" not in segment
    vtt = tmp_path / "out.vtt"
    assert main(["export-transcript", str(output), "--output", str(vtt)]) == 0
    assert "00:00:02.000 --> 00:00:04.000" in vtt.read_text()


@pytest.mark.parametrize("start,end", [(2, 2), (3, 2), (None, 2), (0, 0.000001)])
def test_bad_native_segment_bounds_preserve_previous_output(
    tmp_path, monkeypatch, start, end
):
    media = tmp_path / "in.m4a"
    media.touch()
    output = tmp_path / "out.json"
    output.write_text("previous output")
    monkeypatch.setattr(transcribe, "probe_audio_sample_rate", lambda _: 48000)
    monkeypatch.setattr(
        transcribe,
        "transcribe_faster_whisper",
        lambda *args: [
            {
                "id": "segment",
                "source": "clip01",
                "start": start,
                "end": end,
                "text": "Bonjour.",
                "words": [],
            }
        ],
    )
    assert (
        main(
            [
                "transcribe",
                str(media),
                "--output",
                str(output),
                "--language",
                "fr",
                "--timestamps",
                "segments",
            ]
        )
        == 1
    )
    assert output.read_text() == "previous output"


@pytest.mark.parametrize(
    "flags",
    [
        ["--word-aligner", "ctc"],
        ["--backend", "transformers"],
        ["--recognition-profile", "trained-verbatim"],
        ["--tag-fillers"],
        ["--max-segment-duration", "2"],
    ],
)
def test_incompatible_modes_fail_before_model_loading(tmp_path, monkeypatch, flags):
    media = tmp_path / "in.m4a"
    media.touch()
    monkeypatch.setattr(
        transcribe, "_load_whisper_model", lambda: pytest.fail("No inference allowed")
    )
    assert (
        main(
            [
                "transcribe",
                str(media),
                "--output",
                str(tmp_path / "out.json"),
                "--timestamps",
                "segments",
                *flags,
            ]
        )
        == 1
    )
