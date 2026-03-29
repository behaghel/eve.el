from __future__ import annotations

import json
import subprocess
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

import pytest

from eve_cli.commands import trim_fillers
from eve_cli.main import build_parser, main


class FakeWord:
    def __init__(self, start: float | None, end: float | None, word: str) -> None:
        self.start = start
        self.end = end
        self.word = word


class FakeSegment:
    def __init__(self, words: list[FakeWord]) -> None:
        self.words = words


def test_trim_fillers_parser_matches_legacy_defaults() -> None:
    parser = build_parser()
    args = parser.parse_args(["trim-fillers", "in.mp4", "out.mp4"])

    assert args.command == "trim-fillers"
    assert args.input == "in.mp4"
    assert args.output == "out.mp4"
    assert args.model == "base.en"
    assert args.language == "en"
    assert args.pad == 0.05
    assert args.filler is None
    assert args.video_codec == "libx264"
    assert args.audio_codec == "aac"
    assert args.list_fillers is False
    assert args.save_ranges is None


def test_trim_fillers_list_fillers_merges_defaults_and_custom() -> None:
    stdout = StringIO()
    with redirect_stdout(stdout):
        exit_code = main(
            ["trim-fillers", "in.mp4", "out.mp4", "--list-fillers", "--filler", "Erm"]
        )

    assert exit_code == 0
    assert stdout.getvalue().strip().splitlines() == ["erm", "uh", "um"]


def test_merge_ranges_merges_overlaps() -> None:
    assert trim_fillers._merge_ranges([(0.0, 0.1), (0.08, 0.2), (0.4, 0.5)]) == [
        (0.0, 0.2),
        (0.4, 0.5),
    ]


def test_has_video_uses_ffprobe(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    media = tmp_path / "sample.mp4"
    media.touch()

    def fake_run(
        cmd: list[str], *, capture_output: bool, check: bool
    ) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess(cmd, 0, stdout=b"0\n", stderr=b"")

    monkeypatch.setattr("eve_cli.commands.trim_fillers.subprocess.run", fake_run)

    assert trim_fillers._has_video(media) is True


def test_trim_fillers_run_without_detected_fillers_copies_input(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[list[str]] = []

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
            word_timestamps: bool,
        ) -> tuple[list[FakeSegment], None]:
            assert beam_size == 5
            assert language == "en"
            assert word_timestamps is True
            return ([FakeSegment([FakeWord(0.0, 0.1, "hello")])], None)

    def fake_run(
        cmd: list[str], check: bool = True, **_: object
    ) -> subprocess.CompletedProcess[bytes]:
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout=b"", stderr=b"")

    monkeypatch.setattr(trim_fillers, "_load_whisper_model", lambda: FakeModel)
    monkeypatch.setattr("eve_cli.commands.trim_fillers.subprocess.run", fake_run)

    input_path = tmp_path / "in.mp4"
    input_path.touch()
    output_path = tmp_path / "out.mp4"

    exit_code = main(["trim-fillers", str(input_path), str(output_path)])

    assert exit_code == 0
    assert calls[-1] == [
        trim_fillers.FFMPEG,
        "-y",
        "-i",
        str(input_path),
        "-c",
        "copy",
        str(output_path),
    ]


def test_trim_fillers_run_with_fillers_saves_ranges_and_renders(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[list[str]] = []

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
            word_timestamps: bool,
        ) -> tuple[list[FakeSegment], None]:
            return (
                [
                    FakeSegment(
                        [
                            FakeWord(0.5, 0.6, "um"),
                            FakeWord(1.0, 1.1, "uh"),
                        ]
                    )
                ],
                None,
            )

    def fake_run(
        cmd: list[str], check: bool = True, **_: object
    ) -> subprocess.CompletedProcess[bytes]:
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout=b"0\n", stderr=b"")

    monkeypatch.setattr(trim_fillers, "_load_whisper_model", lambda: FakeModel)
    monkeypatch.setattr("eve_cli.commands.trim_fillers.subprocess.run", fake_run)
    monkeypatch.setattr(trim_fillers, "_has_video", lambda _: True)

    input_path = tmp_path / "in.mp4"
    input_path.touch()
    output_path = tmp_path / "out.mp4"
    ranges_path = tmp_path / "ranges.json"

    exit_code = main(
        [
            "trim-fillers",
            str(input_path),
            str(output_path),
            "--save-ranges",
            str(ranges_path),
            "--pad",
            "0.1",
        ]
    )

    payload = json.loads(ranges_path.read_text(encoding="utf-8"))

    assert exit_code == 0
    assert payload["removed"] == [
        {"start": 0.4, "end": 0.7},
        {"start": 0.9, "end": 1.2},
    ]
    assert payload["filler_words"] == ["uh", "um"]
    assert "select='not(between(t,0.400,0.700)+between(t,0.900,1.200))'" in " ".join(
        calls[-1]
    )
