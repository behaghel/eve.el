from __future__ import annotations

import json
from argparse import Namespace
from contextlib import redirect_stderr
from io import StringIO
from pathlib import Path

import pytest

from eve_cli.commands import transcribe
from eve_cli.main import build_parser, main


class FakeWord:
    def __init__(self, start: float | None, end: float | None, word: str) -> None:
        self.start = start
        self.end = end
        self.word = word


class FakeSegment:
    def __init__(
        self,
        *,
        start: float,
        end: float,
        text: str,
        words: list[FakeWord],
        speaker: str | None = None,
    ) -> None:
        self.start = start
        self.end = end
        self.text = text
        self.words = words
        self.speaker = speaker


def test_transcribe_parser_matches_legacy_defaults() -> None:
    parser = build_parser()
    args = parser.parse_args(["transcribe", "input.mp4", "--output", "manifest.json"])

    assert args.command == "transcribe"
    assert args.inputs == ["input.mp4"]
    assert args.output == "manifest.json"
    assert args.model == "base.en"
    assert args.language == "en"
    assert args.beam_size == 5
    assert args.device == "auto"
    assert args.max_segment_duration == 0.0
    assert args.pretty is False
    assert args.stub is False


def test_transcribe_missing_input_returns_1_with_legacy_message(tmp_path: Path) -> None:
    output = tmp_path / "manifest.json"
    missing = tmp_path / "missing.mp4"
    stderr = StringIO()

    with redirect_stderr(stderr):
        exit_code = main(["transcribe", str(missing), "--output", str(output)])

    assert exit_code == 1
    assert stderr.getvalue().strip() == f"eve transcribe: input '{missing}' not found"


def test_transcribe_stub_writes_manifest_sources_only(tmp_path: Path) -> None:
    media = tmp_path / "clip.mp4"
    media.touch()
    output = tmp_path / "out" / "manifest.json"

    exit_code = main(["transcribe", "--output", str(output), "--stub", str(media)])

    payload = json.loads(output.read_text(encoding="utf-8"))
    assert exit_code == 0
    assert payload == {
        "version": 1,
        "sources": [{"id": "clip01", "file": str(media)}],
        "segments": [],
    }


def test_transcribe_stub_mode_can_come_from_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    media = tmp_path / "clip.mp4"
    media.touch()
    output = tmp_path / "manifest.json"
    monkeypatch.setenv("VIDEO_TRANSCRIBE_STUB", "1")

    exit_code = main(["transcribe", str(media), "--output", str(output), "--pretty"])

    payload = json.loads(output.read_text(encoding="utf-8"))
    assert exit_code == 0
    assert payload["sources"][0]["file"] == str(media)
    assert payload["segments"] == []


def test_segment_to_dict_splits_long_segments_at_word_boundaries() -> None:
    segment = FakeSegment(
        start=0.0,
        end=2.0,
        text="alpha beta gamma delta",
        words=[
            FakeWord(0.0, 0.4, "alpha"),
            FakeWord(0.4, 0.8, "beta"),
            FakeWord(0.8, 1.2, "gamma"),
            FakeWord(1.2, 1.6, "delta"),
        ],
    )

    slices = transcribe.segment_to_dict("clip01", 1, segment, max_duration=0.7)

    assert [item["id"] for item in slices] == [
        "clip01-s0001-0",
        "clip01-s0001-1",
        "clip01-s0001-2",
        "clip01-s0001-3",
    ]
    assert slices[0]["text"] == "alpha"
    assert slices[0]["start"] == 0.0
    assert slices[0]["end"] == 0.4
    assert slices[1]["text"] == "beta"
    assert slices[1]["start"] == 0.4
    assert slices[1]["end"] == 0.8
    assert slices[0]["speaker"] is None
    assert slices[1]["speaker"] is None
    assert slices[2]["text"] == "gamma"
    assert slices[2]["start"] == 0.8
    assert slices[2]["end"] == 1.2
    assert slices[3]["text"] == "delta"
    assert slices[3]["start"] == 1.2
    assert slices[3]["end"] == 1.6


def test_ffmpeg_extract_invokes_expected_command(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls: list[tuple[object, object, object]] = []

    def fake_run(
        cmd: object,
        *,
        check: object,
        capture_output: object,
    ) -> None:
        calls.append((cmd, check, capture_output))

    monkeypatch.setattr("eve_cli.commands.transcribe.subprocess.run", fake_run)

    source = tmp_path / "input.mp4"
    output = tmp_path / "audio.wav"
    transcribe.ffmpeg_extract(source, output)

    assert len(calls) == 1
    cmd, check, capture_output = calls[0]
    assert cmd == [
        transcribe.FFMPEG,
        "-y",
        "-i",
        str(source),
        "-ac",
        "1",
        "-ar",
        "16000",
        "-vn",
        str(output),
    ]
    assert check is True
    assert capture_output is True


def test_transcribe_run_uses_model_and_emits_manifest(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls: list[tuple[str, int, str, bool, bool]] = []
    extracted: list[tuple[Path, Path]] = []

    class FakeModel:
        def __init__(self, model: str, *, device: str, compute_type: str) -> None:
            assert model == "base.en"
            assert device == "auto"
            assert compute_type == "int8"

        def transcribe(
            self,
            wav_path: str,
            *,
            beam_size: int,
            language: str,
            vad_filter: bool,
            word_timestamps: bool,
        ) -> tuple[list[FakeSegment], None]:
            calls.append((wav_path, beam_size, language, vad_filter, word_timestamps))
            return (
                [
                    FakeSegment(
                        start=0.0,
                        end=0.9,
                        text="ignored text",
                        words=[
                            FakeWord(0.0, 0.4, " hello "),
                            FakeWord(0.4, 0.9, "world"),
                            FakeWord(0.9, 1.0, "   "),
                        ],
                        speaker="spk1",
                    )
                ],
                None,
            )

    def fake_extract(input_path: Path, wav_path: Path) -> None:
        extracted.append((input_path, wav_path))

    monkeypatch.setattr(transcribe, "_load_whisper_model", lambda: FakeModel)
    monkeypatch.setattr(transcribe, "ffmpeg_extract", fake_extract)

    media = tmp_path / "clip.mp4"
    media.touch()
    output = tmp_path / "manifest.json"
    args = Namespace(
        inputs=[str(media)],
        output=str(output),
        model="base.en",
        language="en",
        beam_size=5,
        device="auto",
        pretty=False,
        max_segment_duration=0.0,
        stub=False,
        json=False,
        command="transcribe",
    )
    exit_code = transcribe.run(args)

    manifest_text = output.read_text(encoding="utf-8")
    payload = json.loads(manifest_text)

    assert exit_code == 0
    assert manifest_text.endswith("\n")
    assert extracted and extracted[0][0] == media
    assert calls and calls[0][1:] == (5, "en", False, True)
    assert payload["sources"] == [{"id": "clip01", "file": str(media)}]
    assert payload["segments"] == [
        {
            "id": "clip01-s0001",
            "source": "clip01",
            "start": 0.0,
            "end": 0.9,
            "speaker": "spk1",
            "text": "hello world",
            "words": [
                {"start": 0.0, "end": 0.4, "token": "hello"},
                {"start": 0.4, "end": 0.9, "token": "world"},
            ],
            "tags": [],
            "notes": "",
            "broll": None,
        }
    ]
