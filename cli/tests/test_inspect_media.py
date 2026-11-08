"""Read-only format inference from native streams, not filename extensions."""

from __future__ import annotations

import json
import shutil
import subprocess
import wave
from pathlib import Path

import pytest

from eve_cli.main import main

pytestmark = [
    pytest.mark.media,
    pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="FFmpeg unavailable"),
]


def manifest(folder: Path, sources: list[Path]) -> Path:
    data = {
        "version": "1.1",
        "render": {"filler_policy": "keep", "preserve_short_gaps": 0},
        "sources": [
            {
                "id": str(i),
                "file": p.name,
                "timebase": {"numerator": 1, "denominator": 48000},
            }
            for i, p in enumerate(sources)
        ],
        "segments": [
            {"id": f"s{i}", "source": str(i), "start_tick": 0, "end_tick": 12000}
            for i in range(len(sources))
        ],
    }
    path = folder / "edit.tjm.json"
    path.write_text(json.dumps(data))
    return path


def audio(folder: Path) -> Path:
    # Deliberately misleading extension: content, not suffix, owns media type.
    path = folder / "actually-audio.mp4"
    with wave.open(str(path), "wb") as f:
        f.setparams((1, 2, 48000, 0, "NONE", "not compressed"))
        f.writeframes(b"\0\0" * 24000)
    return path


def video(folder: Path) -> Path:
    path = folder / "video.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=blue:s=32x32:r=5:d=0.5",
            "-an",
            "-c:v",
            "libx264",
            str(path),
        ],
        check=True,
    )
    return path


def test_audio_inspection_uses_streams_and_does_not_change_files(tmp_path, capsys):
    path = manifest(tmp_path, [audio(tmp_path)])
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    assert main(["inspect-media", str(path), "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["output_format"] == "m4a"
    assert result["sources"] == [{"id": "0", "has_audio": True, "has_video": False}]
    assert before == {p.name: p.read_bytes() for p in tmp_path.iterdir()}


def test_video_inspection_selects_mp4(tmp_path, capsys):
    path = manifest(tmp_path, [video(tmp_path)])
    assert main(["inspect-media", str(path), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["output_format"] == "mp4"


def test_unused_and_deleted_video_sources_do_not_force_video(tmp_path, capsys):
    path = manifest(tmp_path, [audio(tmp_path), video(tmp_path)])
    data = json.loads(path.read_text())
    data["segments"][1]["edit"] = {"deleted": True}
    (tmp_path / data["sources"][1]["file"]).unlink()
    path.write_text(json.dumps(data))
    assert main(["inspect-media", str(path), "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["output_format"] == "m4a"
    assert len(result["sources"]) == 1


def test_missing_media_does_not_silently_guess_a_format(tmp_path, capsys):
    path = manifest(tmp_path, [tmp_path / "missing.wav"])
    assert main(["inspect-media", str(path), "--json"]) == 1
    assert "not found" in json.loads(capsys.readouterr().out)["error"]


def test_attached_cover_art_does_not_make_a_recording_video(tmp_path, capsys):
    sound = audio(tmp_path)
    cover = tmp_path / "cover.png"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=blue:s=32x32",
            "-frames:v",
            "1",
            str(cover),
        ],
        check=True,
    )
    recording = tmp_path / "with-cover.m4a"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            str(sound),
            "-i",
            str(cover),
            "-map",
            "0:a:0",
            "-map",
            "1:v:0",
            "-c:a",
            "aac",
            "-c:v",
            "png",
            "-disposition:v:0",
            "attached_pic",
            str(recording),
        ],
        check=True,
    )
    path = manifest(tmp_path, [recording])
    assert main(["inspect-media", str(path), "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["output_format"] == "m4a" and not result["sources"][0]["has_video"]


def test_visual_broll_intent_selects_video_without_rewriting_manifest(tmp_path, capsys):
    path = manifest(tmp_path, [audio(tmp_path)])
    data = json.loads(path.read_text())
    data["segments"][0]["edit"] = {
        "broll": {"file": "visual.mp4", "mode": "replace", "audio": "source"}
    }
    path.write_text(json.dumps(data))
    before = path.read_bytes()
    assert main(["inspect-media", str(path), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["output_format"] == "mp4"
    assert path.read_bytes() == before


def test_inspection_rejects_an_unsupported_tjm_version(tmp_path, capsys):
    path = manifest(tmp_path, [audio(tmp_path)])
    data = json.loads(path.read_text())
    data["version"] = "1.2"
    path.write_text(json.dumps(data))
    assert main(["inspect-media", str(path), "--json"]) == 1
    assert "Unsupported TJM version" in json.loads(capsys.readouterr().out)["error"]


def test_corrupt_media_reports_an_inspection_error(tmp_path, capsys):
    source = tmp_path / "bad.wav"
    source.write_bytes(b"not a media file")
    path = manifest(tmp_path, [source])
    assert main(["inspect-media", str(path), "--json"]) == 1
    assert "Cannot inspect media" in json.loads(capsys.readouterr().out)["error"]


def test_legacy_relative_sources_can_still_be_inspected(tmp_path, capsys):
    path = manifest(tmp_path, [audio(tmp_path)])
    data = json.loads(path.read_text())
    data["version"] = 1
    for segment in data["segments"]:
        segment.update(start=0, end=0.25)
    path.write_text(json.dumps(data))
    assert main(["inspect-media", str(path), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["output_format"] == "m4a"


def test_empty_timeline_does_not_guess_video(tmp_path, capsys):
    path = manifest(tmp_path, [audio(tmp_path)])
    data = json.loads(path.read_text())
    data["segments"] = []
    path.write_text(json.dumps(data))
    assert main(["inspect-media", str(path), "--json"]) == 1
    assert "No retained media" in json.loads(capsys.readouterr().out)["error"]
