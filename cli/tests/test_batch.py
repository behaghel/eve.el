from __future__ import annotations

from argparse import Namespace
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path

import pytest

from eve_cli.commands import batch
from eve_cli.main import build_parser, main


def test_batch_parser_matches_legacy_surface() -> None:
    parser = build_parser()
    args = parser.parse_args(["batch", "clip.mp4"])

    assert args.command == "batch"
    assert args.inputs == ["clip.mp4"]
    assert args.skip_denoise is False
    assert args.skip_trim is False
    assert args.skip_transcribe is False
    assert args.transcribe_manifest is None
    assert args.transcribe_model == ""
    assert args.transcribe_language == ""
    assert args.denoise_dir == ""
    assert args.trim_dir == ""


def test_default_manifest_and_output_paths_match_legacy(tmp_path: Path) -> None:
    clip = tmp_path / "clip.mp4"

    assert batch.default_transcribe_manifest([clip]) == Path("clip.tjm.json")
    assert batch.denoise_out_path(clip, "") == tmp_path / "clip.denoise.mp4"
    assert (
        batch.trim_out_path(tmp_path / "clip.denoise.mp4", "")
        == tmp_path / "clip.denoise.trim.mp4"
    )
    assert (
        batch.denoise_out_path(clip, str(tmp_path / "denoised"))
        == tmp_path / "denoised" / "clip.denoise.mp4"
    )
    assert (
        batch.trim_out_path(clip, str(tmp_path / "trimmed"))
        == tmp_path / "trimmed" / "clip.trim.mp4"
    )


def test_batch_all_stages_disabled_returns_1() -> None:
    stderr = StringIO()
    with redirect_stderr(stderr):
        exit_code = main(
            ["batch", "--skip-transcribe", "--skip-denoise", "--skip-trim", "clip.mp4"]
        )

    assert exit_code == 1
    assert stderr.getvalue().strip() == "eve batch: all stages disabled"


def test_batch_orchestrates_transcribe_denoise_and_trim(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[tuple[str, Namespace]] = []
    clip = tmp_path / "clip.mp4"
    clip.touch()

    def fake_transcribe(args: Namespace) -> int:
        calls.append(("transcribe", args))
        return 0

    def fake_denoise(args: Namespace) -> int:
        calls.append(("denoise", args))
        Path(args.output).touch()
        return 0

    def fake_trim(args: Namespace) -> int:
        calls.append(("trim", args))
        Path(args.output).touch()
        return 0

    monkeypatch.setattr("eve_cli.commands.batch.transcribe.run", fake_transcribe)
    monkeypatch.setattr("eve_cli.commands.batch.denoise.run", fake_denoise)
    monkeypatch.setattr("eve_cli.commands.batch.trim_fillers.run", fake_trim)

    exit_code = main(
        [
            "batch",
            "--transcribe-manifest",
            str(tmp_path / "batch.json"),
            str(clip),
        ]
    )

    assert exit_code == 0
    assert [name for name, _ in calls] == ["transcribe", "denoise", "trim"]
    transcribe_args = calls[0][1]
    denoise_args = calls[1][1]
    trim_args = calls[2][1]
    assert transcribe_args.output == str(tmp_path / "batch.json")
    assert denoise_args.output.endswith("clip.denoise.mp4")
    assert trim_args.output.endswith("clip.denoise.trim.mp4")


def test_batch_skips_missing_inputs_but_processes_existing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    existing = tmp_path / "clip.mp4"
    existing.touch()
    missing = tmp_path / "missing.mp4"
    stdout = StringIO()
    stderr = StringIO()
    calls: list[tuple[str, Namespace]] = []

    def fake_transcribe(args: Namespace) -> int:
        calls.append(("transcribe", args))
        return 0

    def fake_denoise(args: Namespace) -> int:
        calls.append(("denoise", args))
        return 0

    def fake_trim(args: Namespace) -> int:
        calls.append(("trim", args))
        return 0

    monkeypatch.setattr("eve_cli.commands.batch.transcribe.run", fake_transcribe)
    monkeypatch.setattr("eve_cli.commands.batch.denoise.run", fake_denoise)
    monkeypatch.setattr("eve_cli.commands.batch.trim_fillers.run", fake_trim)

    with redirect_stdout(stdout), redirect_stderr(stderr):
        exit_code = main(
            ["batch", "--skip-transcribe", "--skip-trim", str(missing), str(existing)]
        )

    assert exit_code == 0
    assert "eve batch: input" in stderr.getvalue()
    assert [name for name, _ in calls] == ["denoise"]
