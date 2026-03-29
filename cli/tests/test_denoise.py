from __future__ import annotations

import subprocess
from contextlib import redirect_stderr
from io import StringIO
from pathlib import Path

import pytest

from eve_cli.commands import denoise
from eve_cli.main import build_parser, main


def test_denoise_parser_matches_legacy_surface() -> None:
    parser = build_parser()
    args = parser.parse_args(["denoise", "in.mp4"])

    assert args.command == "denoise"
    assert args.input == "in.mp4"
    assert args.output is None
    assert args.model is None
    assert args.extra_filter == ""
    assert args.copy_video is False


def test_denoise_missing_input_returns_1_with_legacy_message(tmp_path: Path) -> None:
    missing = tmp_path / "missing.mp4"
    stderr = StringIO()

    with redirect_stderr(stderr):
        exit_code = main(["denoise", str(missing)])

    assert exit_code == 1
    assert stderr.getvalue().strip() == f"eve denoise: input '{missing}' not found"


def test_denoise_missing_model_returns_1_with_legacy_message(tmp_path: Path) -> None:
    media = tmp_path / "clip.mp4"
    media.touch()
    missing_model = tmp_path / "model.rnnn"
    stderr = StringIO()

    with redirect_stderr(stderr):
        exit_code = main(["denoise", "-m", str(missing_model), str(media)])

    assert exit_code == 1
    assert stderr.getvalue().strip() == (
        f"eve denoise: RNNoise model '{missing_model}' not found"
    )


def test_default_output_path_matches_legacy_naming(tmp_path: Path) -> None:
    assert (
        denoise.default_output_path(tmp_path / "clip.mp4")
        == tmp_path / "clip.denoise.mp4"
    )
    assert denoise.default_output_path(tmp_path / "clip") == tmp_path / "clip.denoise"


def test_denoise_run_builds_video_command(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[list[str]] = []
    model_path = tmp_path / "model.rnnn"
    model_path.touch()

    def fake_run(
        cmd: list[str], check: bool = True, **_: object
    ) -> subprocess.CompletedProcess[bytes]:
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout=b"", stderr=b"")

    monkeypatch.setattr(denoise, "_has_video", lambda _: True)
    monkeypatch.setattr(denoise, "_resolve_model_path", lambda _: model_path)
    monkeypatch.setattr("eve_cli.commands.denoise.subprocess.run", fake_run)

    input_path = tmp_path / "in.mp4"
    input_path.touch()
    output_path = tmp_path / "out.mp4"

    exit_code = main(["denoise", "-C", str(input_path), str(output_path)])

    assert exit_code == 0
    assert calls[-1] == [
        denoise.FFMPEG,
        "-y",
        "-i",
        str(input_path),
        "-filter:a",
        f"arnndn=m={model_path},aresample=async=1:first_pts=0",
        "-c:v",
        "copy",
        "-vsync",
        "passthrough",
        "-c:a",
        "aac",
        "-movflags",
        "+faststart",
        str(output_path),
    ]


def test_denoise_run_builds_audio_only_command(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[list[str]] = []
    model_path = tmp_path / "model.rnnn"
    model_path.touch()

    def fake_run(
        cmd: list[str], check: bool = True, **_: object
    ) -> subprocess.CompletedProcess[bytes]:
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout=b"", stderr=b"")

    monkeypatch.setattr(denoise, "_has_video", lambda _: False)
    monkeypatch.setattr(denoise, "_resolve_model_path", lambda _: model_path)
    monkeypatch.setattr("eve_cli.commands.denoise.subprocess.run", fake_run)

    input_path = tmp_path / "in.wav"
    input_path.touch()

    exit_code = main(["denoise", "-f", "volume=0.5", str(input_path)])

    assert exit_code == 0
    assert calls[-1] == [
        denoise.FFMPEG,
        "-y",
        "-i",
        str(input_path),
        "-af",
        f"arnndn=m={model_path},aresample=async=1:first_pts=0,volume=0.5",
        str(tmp_path / "in.denoise.wav"),
    ]


def test_resolve_model_path_downloads_default(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    target = tmp_path / ".cache-home"
    download_calls: list[tuple[str, Path]] = []

    def fake_urlretrieve(url: str, destination: Path) -> None:
        download_calls.append((url, destination))
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text("model", encoding="utf-8")

    monkeypatch.setenv("HOME", str(target))
    monkeypatch.setattr("urllib.request.urlretrieve", fake_urlretrieve)

    resolved = denoise._resolve_model_path(None)

    assert resolved == target / ".cache" / "eve-cli" / "rnnoise" / "sh.rnnn"
    assert download_calls == [(denoise.DEFAULT_MODEL_URL, resolved)]
