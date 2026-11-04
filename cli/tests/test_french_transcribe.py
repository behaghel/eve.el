"""French recognition must not inherit English-only models or filler prompts."""

from pathlib import Path

import pytest

from eve_cli.commands import transcribe
from eve_cli.main import build_parser, main


@pytest.mark.parametrize("language,expected", [("en", "medium.en"), ("fr", "medium")])
def test_language_selects_an_appropriate_default_model(
    tmp_path, monkeypatch, language, expected
):
    media = tmp_path / "audio.m4a"
    media.touch()
    observed = []
    monkeypatch.setattr(transcribe, "probe_audio_sample_rate", lambda _: 48000)

    def recognize(args, inputs):
        observed.append(args.model)
        return []

    monkeypatch.setattr(transcribe, "transcribe_faster_whisper", recognize)
    assert (
        main(
            [
                "transcribe",
                str(media),
                "--language",
                language,
                "--output",
                str(tmp_path / "out.json"),
            ]
        )
        == 0
    )
    assert observed == [expected]


def test_explicit_english_only_model_is_rejected_for_french(
    tmp_path, monkeypatch, capsys
):
    media = tmp_path / "audio.m4a"
    media.touch()
    monkeypatch.setattr(
        transcribe,
        "_load_whisper_model",
        lambda: pytest.fail("must fail before inference"),
    )
    assert (
        main(
            [
                "transcribe",
                str(media),
                "--language",
                "fr",
                "--model",
                "base.en",
                "--output",
                str(tmp_path / "out.json"),
            ]
        )
        == 1
    )
    assert "English-only" in capsys.readouterr().err


def test_french_uses_timestamps_without_english_verbatim_bias(tmp_path, monkeypatch):
    calls = []

    class Model:
        def __init__(self, *args, **kwargs):
            pass

        def transcribe(self, path, **kwargs):
            calls.append(kwargs)
            return [], None

    monkeypatch.setattr(transcribe, "_load_whisper_model", lambda: Model)
    monkeypatch.setattr(transcribe, "ffmpeg_extract", lambda *args: None)
    args = build_parser().parse_args(
        [
            "transcribe",
            "audio.m4a",
            "--language",
            "fr",
            "--model",
            "large-v3",
            "--output",
            "out.json",
        ]
    )
    transcribe.transcribe_faster_whisper(args, [Path("audio.m4a")])
    assert calls[0]["language"] == "fr"
    assert calls[0]["word_timestamps"] is True
    assert "initial_prompt" not in calls[0]
    assert "hotwords" not in calls[0]
    assert calls[0]["condition_on_previous_text"] is False
    args.hotwords = "catéchuménat sacrements"
    transcribe.transcribe_faster_whisper(args, [Path("audio.m4a")])
    assert calls[1]["hotwords"] == "catéchuménat sacrements"


def test_transformers_french_has_no_english_prompt():
    args = build_parser().parse_args(
        ["transcribe", "audio.m4a", "--language", "fr", "--output", "out.json"]
    )
    assert transcribe._build_transformers_generate_kwargs(object(), args) == {
        "language": "fr"
    }
