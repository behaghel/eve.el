from __future__ import annotations

import json
import subprocess
from argparse import Namespace
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from typing import Any

import pytest

from eve_cli.commands import text_edit
from eve_cli.main import build_parser, main


def test_text_edit_parser_matches_legacy_surface() -> None:
    parser = build_parser()
    args = parser.parse_args(["text-edit", "edit.tjm.json", "--output", "final.mp4"])

    assert args.command == "text-edit"
    assert args.manifest == "edit.tjm.json"
    assert args.output == "final.mp4"
    assert args.workdir is None
    assert args.pretty_manifest is None
    assert args.preserve_short_gaps is None
    assert args.subtitles is None
    assert args.no_subtitle_mux is False


def test_build_subtitle_cues_and_markers_preserve_short_gaps() -> None:
    manifest = {
        "version": 1,
        "sources": [{"id": "clip01", "file": "sample.mp4"}],
        "segments": [
            {
                "id": "clip01-s0001",
                "source": "clip01",
                "start": 0.0,
                "end": 0.6,
                "text": "segment one",
            },
            {
                "id": "clip01-s0002",
                "source": "clip01",
                "start": 0.9,
                "end": 1.5,
                "text": "segment two",
            },
            {
                "id": "marker-001",
                "kind": "marker",
                "title": "Break",
            },
            {
                "id": "clip01-s0003",
                "source": "clip01",
                "start": 1.5,
                "end": 2.0,
                "text": "segment three",
            },
        ],
    }

    cues = text_edit.build_subtitle_cues(manifest, preserve_gap_threshold=0.5)
    markers = text_edit.collect_markers(manifest, preserve_gap_threshold=0.5)

    assert cues == [
        (0.0, 0.6, "segment one"),
        (0.9, 1.5, "segment two"),
        (1.5, 2.0, "segment three"),
    ]
    assert markers == [("Break", 1.5)]


def test_run_orchestrates_render_subtitles_and_manifest(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    manifest = {
        "version": 1,
        "sources": [{"id": "clip01", "file": "sample.mp4"}],
        "segments": [
            {
                "id": "clip01-s0001",
                "source": "clip01",
                "start": 0.0,
                "end": 0.5,
                "text": "hello",
            }
        ],
    }
    output = tmp_path / "out" / "final.mp4"
    pretty_manifest = tmp_path / "out" / "pretty.json"
    calls: dict[str, Any] = {}

    def fake_load_manifest(path: Path) -> dict[str, object]:
        calls["manifest_path"] = path
        return manifest

    def fake_render_segments(
        payload: dict[str, object],
        base_dir: Path,
        working: Path,
        preserve_gap_threshold: float | None = None,
    ) -> list[Path]:
        calls["render"] = (payload, base_dir, working, preserve_gap_threshold)
        segment = working / "segment_0001.mp4"
        segment.write_text("segment", encoding="utf-8")
        return [segment]

    def fake_concat_segments(segments: list[Path], destination: Path) -> None:
        calls["concat"] = (segments, destination)
        destination.write_text("video", encoding="utf-8")

    def fake_write_webvtt(cues: list[tuple[float, float, str]], path: Path) -> None:
        calls["subtitles"] = (cues, path)
        path.write_text("WEBVTT\n\n", encoding="utf-8")

    def fake_mux_subtitles(video_path: Path, subtitles_path: Path) -> None:
        calls["mux"] = (video_path, subtitles_path)

    monkeypatch.setattr(text_edit, "load_manifest", fake_load_manifest)
    monkeypatch.setattr(text_edit, "render_segments", fake_render_segments)
    monkeypatch.setattr(text_edit, "concat_segments", fake_concat_segments)
    monkeypatch.setattr(text_edit, "write_webvtt", fake_write_webvtt)
    monkeypatch.setattr(text_edit, "mux_subtitles", fake_mux_subtitles)

    args = Namespace(
        manifest=str(tmp_path / "edit.tjm.json"),
        output=str(output),
        workdir=None,
        pretty_manifest=str(pretty_manifest),
        preserve_short_gaps=0.5,
        subtitles="",
        no_subtitle_mux=False,
        json=False,
        command="text-edit",
    )

    exit_code = text_edit.run(args)

    assert exit_code == 0
    assert output.read_text(encoding="utf-8") == "video"
    assert json.loads(pretty_manifest.read_text(encoding="utf-8")) == manifest
    assert calls["manifest_path"] == Path(args.manifest)
    assert calls["concat"][1] == output
    subtitle_cues, subtitle_path = calls["subtitles"]
    assert subtitle_cues == [(0.0, 0.5, "hello")]
    assert subtitle_path == output.with_suffix(".vtt")
    assert calls["mux"] == (output, output.with_suffix(".vtt"))


def test_parse_timecode_and_display_helpers() -> None:
    assert text_edit.parse_timecode(None, default=1.25) == 1.25
    assert text_edit.parse_timecode(3) == 3.0
    assert text_edit.parse_timecode("1.5") == 1.5
    assert text_edit.parse_timecode("01:02") == 62.0
    assert text_edit.parse_timecode("01:02:03.5") == 3723.5
    assert text_edit.segment_filename(7) == "segment_0007.mp4"
    assert text_edit.format_timestamp(62.345) == "00:01:02.345"
    assert text_edit.format_minsec(125.1) == "02:05"
    assert text_edit.cue_text({"speaker": "Ari", "text": "hello"}) == "Ari: hello"
    assert (
        text_edit.cue_text({"words": [{"token": "alpha"}, {"token": "beta"}]})
        == "alpha beta"
    )
    with pytest.raises(ValueError):
        text_edit.parse_timecode("1:2:3:4")


def test_probe_video_characteristics_caches_results(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    media = tmp_path / "sample.mp4"
    media.touch()
    calls: list[list[str]] = []
    payload = json.dumps(
        {
            "streams": [
                {
                    "width": 320,
                    "height": 240,
                    "r_frame_rate": "30/1",
                    "pix_fmt": "yuv420p",
                }
            ]
        }
    )

    def fake_run(
        cmd: list[str], *, check: bool, capture_output: bool, text: bool
    ) -> subprocess.CompletedProcess[str]:
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout=payload, stderr="")

    monkeypatch.setattr("eve_cli.commands.text_edit.subprocess.run", fake_run)
    text_edit._VIDEO_PROBE_CACHE.clear()

    first = text_edit.probe_video_characteristics(media)
    second = text_edit.probe_video_characteristics(media)

    assert first == second
    assert first["width"] == 320
    assert len(calls) == 1


def test_run_ffmpeg_wraps_process_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(
        cmd: list[str], *, check: bool, capture_output: bool, text: bool
    ) -> None:
        raise subprocess.CalledProcessError(1, cmd, stderr="bad filter")

    monkeypatch.setattr("eve_cli.commands.text_edit.subprocess.run", fake_run)

    with pytest.raises(RuntimeError, match="while trimming"):
        text_edit.run_ffmpeg(["ffmpeg"], context="trimming")


def test_load_broll_spec_and_drawtext_filters(tmp_path: Path) -> None:
    template = tmp_path / "card.json"
    template.write_text(
        json.dumps(
            {
                "template": str(tmp_path / "card.mp4"),
                "placeholders": {"title": "Weekly Update"},
                "overlays": [{"placeholder": "title", "align": "center"}],
            }
        ),
        encoding="utf-8",
    )

    spec = text_edit.load_broll_spec(
        {
            "file": str(template),
            "placeholders": {"title": "Launch"},
        }
    )
    filters = text_edit.build_drawtext_filters(spec["overlays"], spec["placeholders"])

    assert spec["media_path"] == tmp_path / "card.mp4"
    assert spec["placeholders"]["title"] == "Launch"
    assert "drawtext=" in filters
    assert "Launch" in filters


def test_build_drawtext_filters_with_full_overlay_options(tmp_path: Path) -> None:
    font = tmp_path / "font.ttf"
    font.touch()

    filters = text_edit.build_drawtext_filters(
        [
            {
                "placeholder": "title",
                "font": str(font),
                "fontsize": 36,
                "color": "white",
                "align": "center",
                "boxColor": "black@0.4",
                "shadowColor": "black",
                "shadowX": 2,
                "shadowY": 3,
                "start": 0.5,
                "duration": 1.0,
            }
        ],
        {"title": "Launch"},
    )

    assert "fontfile=" in filters
    assert "fontsize=36" in filters
    assert "fontcolor=white" in filters
    assert "box=1" in filters
    assert "shadowcolor=black" in filters
    assert "enable='between(t,0.5,1.5)'" in filters


def test_prepare_broll_media_and_build_commands(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    media = tmp_path / "pip.mp4"
    media.touch()
    still = tmp_path / "still.png"
    still.touch()
    source = tmp_path / "source.mp4"
    source.touch()
    calls: list[list[str]] = []

    def fake_probe(_path: Path) -> dict[str, Any]:
        return {
            "width": 320,
            "height": 240,
            "fps": 30.0,
            "fps_str": "30/1",
            "pix_fmt": "yuv420p",
        }

    def fake_run_ffmpeg(cmd: list[str], *, context: str | None = None) -> None:
        calls.append(cmd)

    monkeypatch.setattr(text_edit, "probe_video_characteristics", fake_probe)
    monkeypatch.setattr(text_edit, "run_ffmpeg", fake_run_ffmpeg)

    prepared_path, pix_fmt = text_edit.prepare_broll_media(
        {"file": str(still), "still": True},
        fake_probe(source),
        0.6,
        tmp_path,
        "source",
    )
    replace_cmd = text_edit.build_broll_command(
        source,
        0.0,
        0.6,
        {"file": str(media), "mode": "replace"},
        tmp_path / "replace.mp4",
        tmp_path,
    )
    pip_cmd = text_edit.build_broll_command(
        source,
        0.0,
        0.6,
        {
            "file": str(media),
            "mode": "pip",
            "position": {"x": 0.7, "y": 0.7, "width": 0.25},
        },
        tmp_path / "pip-out.mp4",
        tmp_path,
    )

    assert prepared_path.suffix == ".mp4"
    assert pix_fmt == "yuv420p"
    assert calls and calls[0][0] == text_edit.FFMPEG
    assert "overlay=" in " ".join(pip_cmd)
    assert replace_cmd[0] == text_edit.FFMPEG

    with pytest.raises(ValueError, match="pip width"):
        text_edit.build_broll_command(
            source,
            0.0,
            0.6,
            {
                "file": str(media),
                "mode": "pip",
                "position": {"x": 0.5, "y": 0.5, "width": 1.2},
            },
            tmp_path / "bad.mp4",
            tmp_path,
        )


def test_render_concat_and_marker_helpers(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    source = tmp_path / "sample.mp4"
    source.touch()
    broll = tmp_path / "pip.mp4"
    broll.touch()
    commands: list[tuple[list[str], str | None]] = []

    def fake_probe(_path: Path) -> dict[str, Any]:
        return {
            "width": 320,
            "height": 240,
            "fps": 30.0,
            "fps_str": "30/1",
            "pix_fmt": "yuv420p",
        }

    def fake_run_ffmpeg(cmd: list[str], *, context: str | None = None) -> None:
        commands.append((cmd, context))
        Path(cmd[-1]).write_text("clip", encoding="utf-8")

    monkeypatch.setattr(text_edit, "probe_video_characteristics", fake_probe)
    monkeypatch.setattr(text_edit, "run_ffmpeg", fake_run_ffmpeg)

    manifest = {
        "version": 1,
        "sources": [{"id": "clip01", "file": str(source)}],
        "segments": [
            {
                "id": "clip01-s0001",
                "source": "clip01",
                "start": 0.0,
                "end": 0.5,
                "text": "one",
            },
            {"id": "marker-001", "kind": "marker", "title": "Break"},
            {
                "id": "clip01-s0002",
                "source": "clip01",
                "start": 0.7,
                "end": 1.1,
                "text": "two",
                "broll": {"file": str(broll), "mode": "replace"},
            },
        ],
    }

    rendered = text_edit.render_segments(
        manifest, tmp_path, tmp_path, preserve_gap_threshold=0.3
    )
    text_edit.concat_segments(rendered, tmp_path / "final.mp4")
    markers = text_edit.collect_markers(manifest, preserve_gap_threshold=0.3)
    text_edit.write_manifest(manifest, tmp_path / "pretty.json")

    assert rendered
    assert any("preserving gap" in (context or "") for _, context in commands)
    assert any("concatenating rendered segments" == context for _, context in commands)
    assert markers == [("Break", 0.5)]
    assert (
        json.loads((tmp_path / "pretty.json").read_text(encoding="utf-8")) == manifest
    )


def test_text_edit_helper_error_paths(tmp_path: Path) -> None:
    source = tmp_path / "source.mp4"
    source.touch()
    broll = tmp_path / "broll.mp4"
    broll.touch()

    assert text_edit.canonical_broll_key({"id": "seg-1"}) is None
    assert text_edit.canonical_broll_key({"broll": {"file": str(broll)}}) == (
        str(broll),
        "replace",
        "source",
        False,
        (),
        False,
    )
    assert text_edit.segment_overlay_duration({"broll": {"duration": 1.2}}) == 1.2

    with pytest.raises(ValueError, match="missing 'file'"):
        text_edit.load_broll_spec({})

    with pytest.raises(RuntimeError, match="No subtitle cues"):
        text_edit.write_webvtt([], tmp_path / "empty.vtt")

    with pytest.raises(ValueError, match="Unsupported b-roll mode"):
        text_edit.build_broll_command(
            source,
            0.0,
            0.5,
            {"file": str(broll), "mode": "zoom"},
            tmp_path / "bad-mode.mp4",
            tmp_path,
        )

    with pytest.raises(ValueError, match="Unsupported b-roll audio"):
        text_edit.build_broll_command(
            source,
            0.0,
            0.5,
            {"file": str(broll), "mode": "replace", "audio": "nope"},
            tmp_path / "bad-audio.mp4",
            tmp_path,
        )


def test_text_edit_end_to_end_render(tmp_path: Path) -> None:
    sample = tmp_path / "sample.mp4"
    still = tmp_path / "still.png"
    pip = tmp_path / "pip.mp4"
    manifest_path = tmp_path / "edit.tjm.json"
    output = tmp_path / "final.mp4"
    subtitles = tmp_path / "final.vtt"

    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=320x240:rate=30:duration=3",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=3",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-movflags",
            "+faststart",
            str(sample),
        ],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=blue:s=320x240",
            "-frames:v",
            "1",
            str(still),
        ],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=160x120:rate=30",
            "-t",
            "2",
            str(pip),
        ],
        check=True,
        capture_output=True,
    )

    manifest = {
        "version": 1,
        "sources": [{"id": "clip01", "file": str(sample)}],
        "segments": [
            {
                "id": "clip01-s0001",
                "source": "clip01",
                "start": 0.0,
                "end": 0.6,
                "text": "segment one",
                "words": [
                    {"start": 0.0, "end": 0.3, "token": "segment"},
                    {"start": 0.3, "end": 0.6, "token": "one"},
                ],
                "tags": [],
                "notes": "",
                "broll": None,
            },
            {
                "id": "clip01-s0002",
                "source": "clip01",
                "start": 0.9,
                "end": 1.5,
                "text": "segment two",
                "words": [
                    {"start": 0.9, "end": 1.2, "token": "segment"},
                    {"start": 1.2, "end": 1.5, "token": "two"},
                ],
                "tags": [],
                "notes": "",
                "broll": {
                    "file": str(still),
                    "mode": "replace",
                    "still": True,
                    "duration": "0:00:00.6",
                },
            },
            {
                "id": "clip01-s0003",
                "source": "clip01",
                "start": 1.5,
                "end": 2.0,
                "text": "segment three",
                "words": [
                    {"start": 1.5, "end": 1.7, "token": "segment"},
                    {"start": 1.7, "end": 2.0, "token": "three"},
                ],
                "tags": [],
                "notes": "",
                "broll": {
                    "file": str(pip),
                    "mode": "pip",
                    "start_offset": 0.2,
                    "duration": 0.5,
                    "position": {"x": 0.7, "y": 0.7, "width": 0.25},
                },
            },
            {"id": "marker-001", "kind": "marker", "title": "Break"},
            {
                "id": "clip01-s0004",
                "source": "clip01",
                "start": 2.0,
                "end": 2.5,
                "text": "segment four",
                "words": [
                    {"start": 2.0, "end": 2.2, "token": "segment"},
                    {"start": 2.2, "end": 2.5, "token": "four"},
                ],
                "tags": [],
                "notes": "",
                "broll": {
                    "file": str(pip),
                    "mode": "pip",
                    "continue": True,
                    "duration": 0.5,
                    "position": {"x": 0.7, "y": 0.7, "width": 0.25},
                },
            },
        ],
    }
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    stdout = StringIO()
    with redirect_stdout(stdout):
        exit_code = main(
            [
                "text-edit",
                str(manifest_path),
                "--output",
                str(output),
                "--subtitles",
                str(subtitles),
                "--preserve-short-gaps",
                "0.5",
                "--pretty-manifest",
                str(tmp_path / "pretty.json"),
            ]
        )

    render_log = stdout.getvalue()

    assert exit_code == 0
    assert output.exists() and output.stat().st_size > 0
    assert subtitles.exists() and subtitles.stat().st_size > 0
    assert "[00:02] Break" in render_log
    assert "segment clip01-s0004" in render_log
    assert "before segment clip01-s0004" not in render_log
    assert "WEBVTT" in subtitles.read_text(encoding="utf-8")
