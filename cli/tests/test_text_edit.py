from __future__ import annotations

import json
import shutil
import subprocess
from argparse import Namespace
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from typing import Any

import pytest

from eve_cli.commands import text_edit
from eve_cli.main import build_parser, main


def _manifest_with_deleted_edits(source_file: str) -> dict[str, Any]:
    return {
        "version": 1,
        "sources": [{"id": "clip01", "file": source_file}],
        "segments": [
            {
                "id": "clip01-s0001",
                "source": "clip01",
                "start": 0.0,
                "end": 0.5,
                "text": "keep one",
            },
            {
                "id": "clip01-s0002",
                "source": "clip01",
                "start": 0.5,
                "end": 1.0,
                "text": "delete me",
                "edit": {"deleted": True},
            },
            {"id": "marker-001", "kind": "marker", "title": "After delete"},
            {
                "id": "clip01-s0003",
                "source": "clip01",
                "start": 1.0,
                "end": 1.5,
                "words": [
                    {"start": 1.0, "end": 1.15, "token": "visible"},
                    {
                        "start": 1.15,
                        "end": 1.3,
                        "token": "ghost",
                        "edit": {"deleted": True},
                    },
                    {"start": 1.3, "end": 1.5, "token": "words"},
                ],
            },
        ],
    }


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
                "edit": {"deleted": False, "tags": [], "notes": "", "broll": None},
                "words": [
                    {
                        "start": 0.0,
                        "end": 0.25,
                        "token": "hello",
                        "edit": {"deleted": False},
                    },
                    {
                        "start": 0.25,
                        "end": 0.5,
                        "token": "there",
                        "edit": {"deleted": True},
                    },
                ],
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
        quality: str = "draft",
        jobs: int = 0,
        cache_dir: Path | None = None,
        scale: float = 1.0,
        codec: str = "h264",
        resume: bool = True,
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
        quality="draft",
        jobs=0,
        command="text-edit",
    )

    exit_code = text_edit.run(args)

    assert exit_code == 0
    assert output.read_text(encoding="utf-8") == "video"
    assert json.loads(pretty_manifest.read_text(encoding="utf-8")) == manifest
    assert (
        json.loads(pretty_manifest.read_text(encoding="utf-8"))["segments"][0]["edit"]
        == manifest["segments"][0]["edit"]
    )
    assert (
        json.loads(pretty_manifest.read_text(encoding="utf-8"))["segments"][0]["words"][
            1
        ]["edit"]
        == manifest["segments"][0]["words"][1]["edit"]
    )
    assert calls["manifest_path"] == Path(args.manifest)
    assert calls["concat"][1] == output
    subtitle_cues, subtitle_path = calls["subtitles"]
    assert subtitle_cues == [(0.0, 0.25, "hello")]
    assert subtitle_path == output.with_suffix(".vtt")
    assert calls["mux"] == (output, output.with_suffix(".vtt"))


def test_render_segments_skips_deleted_segments(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    source = tmp_path / "sample.mp4"
    source.touch()
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

    rendered = text_edit.render_segments(
        _manifest_with_deleted_edits(str(source)),
        tmp_path,
        tmp_path,
    )

    assert [path.name for path in rendered] == ["segment_0001.mp4", "segment_0002.mp4"]
    assert not any("clip01-s0002" in (context or "") for _, context in commands)
    partial_commands = [
        cmd
        for cmd, context in commands
        if context and "clip01-s0003" in context and "(part" in context
    ]
    assert [cmd[2:6] for cmd in partial_commands] == [
        ["-ss", "1.000", "-to", "1.150"],
        ["-ss", "1.300", "-to", "1.500"],
    ]
    assert any(context == "concatenating rendered segments" for _, context in commands)


def test_build_subtitle_cues_skip_deleted_segments_and_words() -> None:
    cues = text_edit.build_subtitle_cues(
        _manifest_with_deleted_edits("sample.mp4"),
    )

    assert cues[0] == (0.0, 0.5, "keep one")
    assert cues[1][0] == 0.5
    assert cues[1][1] == pytest.approx(0.85)
    assert cues[1][2] == "visible words"


def test_collect_markers_skips_deleted_segments() -> None:
    markers = text_edit.collect_markers(
        _manifest_with_deleted_edits("sample.mp4"),
    )

    assert markers == [("After delete", 0.5)]


def test_collect_markers_skips_deleted_markers() -> None:
    manifest = _manifest_with_deleted_edits("sample.mp4")
    manifest["segments"][2]["edit"] = {
        "deleted": True,
        "broll": {"file": "deleted-marker.mp4", "mode": "replace", "duration": 1.0},
    }

    markers = text_edit.collect_markers(manifest)

    assert markers == []


def test_render_segments_ignores_deleted_marker_broll_chain_state(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    source = tmp_path / "sample.mp4"
    source.touch()
    broll = tmp_path / "pip.mp4"
    broll.touch()
    prepared = tmp_path / "prepared.mp4"
    prepared.touch()
    commands: list[tuple[list[str], str | None]] = []
    effective_offsets: list[float | None] = []

    def fake_probe(_path: Path) -> dict[str, Any]:
        return {
            "width": 320,
            "height": 240,
            "fps": 30.0,
            "fps_str": "30/1",
            "pix_fmt": "yuv420p",
        }

    def fake_prepare_broll_media(
        _broll: dict[str, Any],
        _source_info: dict[str, Any] | None,
        _total_duration: float,
        _working: Path,
        _audio_policy: str,
        _quality: str = "draft",
        _scale: float = 1.0,
        _codec: str = "h264",
    ) -> tuple[Path, str]:
        return prepared, "yuv420p"

    def fake_build_broll_command(
        _source: Path,
        _start: float,
        _end: float,
        _broll: dict[str, Any],
        dest: Path,
        _working: Path,
        *,
        effective_offset: float | None = None,
        effective_duration: float | None = None,
        quality: str = "draft",
        scale: float = 1.0,
        codec: str = "h264",
    ) -> list[str]:
        effective_offsets.append(effective_offset)
        assert effective_duration == 0.5
        return [text_edit.FFMPEG, "-y", str(dest)]

    def fake_run_ffmpeg(cmd: list[str], *, context: str | None = None) -> None:
        commands.append((cmd, context))
        Path(cmd[-1]).write_text("clip", encoding="utf-8")

    monkeypatch.setattr(text_edit, "probe_video_characteristics", fake_probe)
    monkeypatch.setattr(text_edit, "prepare_broll_media", fake_prepare_broll_media)
    monkeypatch.setattr(text_edit, "build_broll_command", fake_build_broll_command)
    monkeypatch.setattr(text_edit, "run_ffmpeg", fake_run_ffmpeg)

    manifest = {
        "version": 1,
        "sources": [{"id": "clip01", "file": str(source)}],
        "segments": [
            {
                "id": "marker-001",
                "kind": "marker",
                "title": "Deleted intro",
                "edit": {
                    "deleted": True,
                    "broll": {
                        "file": str(broll),
                        "mode": "replace",
                        "continue": True,
                        "duration": 0.8,
                    },
                },
            },
            {
                "id": "clip01-s0001",
                "source": "clip01",
                "start": 0.0,
                "end": 0.5,
                "text": "segment one",
                "edit": {
                    "broll": {
                        "file": str(broll),
                        "mode": "replace",
                        "continue": True,
                        "duration": 0.5,
                    }
                },
            },
        ],
    }

    rendered = text_edit.render_segments(manifest, tmp_path, tmp_path)

    assert [path.name for path in rendered] == ["segment_0001.mp4"]
    assert effective_offsets == [0.0]
    assert not any("marker-001" in (context or "") for _, context in commands)


def test_collect_markers_use_surviving_word_duration() -> None:
    manifest = _manifest_with_deleted_edits("sample.mp4")
    manifest["segments"].append(
        {"id": "marker-002", "kind": "marker", "title": "After partial delete"}
    )

    markers = text_edit.collect_markers(manifest)

    assert markers[0] == ("After delete", 0.5)
    assert markers[1][0] == "After partial delete"
    assert markers[1][1] == pytest.approx(0.85)


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


def test_text_edit_prefers_nested_segment_edit_broll_with_legacy_fallback(
    tmp_path: Path,
) -> None:
    legacy = tmp_path / "legacy.mp4"
    legacy.touch()
    nested = tmp_path / "nested.mp4"
    nested.touch()

    segment = {
        "id": "seg-1",
        "broll": {"file": str(legacy), "duration": 9.0},
        "edit": {"broll": {"file": str(nested), "duration": 1.2}},
    }

    assert text_edit.normalized_segment_broll(segment) == {
        "file": str(nested),
        "duration": 1.2,
    }
    assert text_edit.canonical_broll_key(segment) == (
        str(nested),
        "replace",
        "source",
        False,
        (),
        False,
    )
    assert text_edit.segment_overlay_duration(segment) == 1.2
    assert text_edit.normalized_segment_broll({"broll": {"file": str(legacy)}}) == {
        "file": str(legacy)
    }
    assert (
        text_edit.normalized_segment_broll(
            {"broll": {"file": str(legacy)}, "edit": {"broll": None}}
        )
        is None
    )


@pytest.mark.skipif(
    shutil.which("ffmpeg") is None,
    reason="ffmpeg not available",
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


def test_jobs_arg_defaults_to_zero() -> None:
    parser = build_parser()
    args = parser.parse_args(["text-edit", "edit.tjm.json", "--output", "out.mp4"])
    assert args.jobs == 0


def test_jobs_arg_accepts_value() -> None:
    parser = build_parser()
    args = parser.parse_args(
        ["text-edit", "edit.tjm.json", "--output", "out.mp4", "--jobs", "4"]
    )
    assert args.jobs == 4


def test_render_segments_parallel_preserves_order(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    source = tmp_path / "sample.mp4"
    source.touch()
    render_order: list[str] = []

    def fake_probe(_path: Path) -> dict[str, Any]:
        return {
            "width": 320,
            "height": 240,
            "fps": 30.0,
            "fps_str": "30/1",
            "pix_fmt": "yuv420p",
        }

    def fake_run_ffmpeg(cmd: list[str], *, context: str | None = None) -> None:
        out = Path(cmd[-1])
        out.write_text("clip", encoding="utf-8")
        render_order.append(out.name)

    monkeypatch.setattr(text_edit, "probe_video_characteristics", fake_probe)
    monkeypatch.setattr(text_edit, "run_ffmpeg", fake_run_ffmpeg)

    manifest = {
        "version": 1,
        "sources": [{"id": "clip01", "file": str(source)}],
        "segments": [
            {
                "id": f"clip01-s{i:04d}",
                "source": "clip01",
                "start": float(i) * 0.5,
                "end": float(i) * 0.5 + 0.5,
                "text": f"segment {i}",
            }
            for i in range(6)
        ],
    }

    outputs = text_edit.render_segments(manifest, tmp_path, tmp_path, jobs=4)

    assert len(outputs) == 6
    for i, path in enumerate(outputs):
        assert path == tmp_path / f"segment_{i + 1:04d}.mp4", (
            f"Output {i} is {path.name}, expected segment_{i + 1:04d}.mp4"
        )


def test_quality_arg_defaults_to_draft() -> None:
    parser = build_parser()
    args = parser.parse_args(["text-edit", "edit.tjm.json", "--output", "out.mp4"])
    assert args.quality == "draft"


def test_quality_arg_accepts_final() -> None:
    parser = build_parser()
    args = parser.parse_args(
        ["text-edit", "edit.tjm.json", "--output", "out.mp4", "--quality", "final"]
    )
    assert args.quality == "final"


def test_encoding_params_draft() -> None:
    params = text_edit.encoding_params("draft")
    assert params["preset"] == "ultrafast"
    assert params["crf"] == "28"


def test_encoding_params_final() -> None:
    params = text_edit.encoding_params("final")
    assert params["preset"] == "medium"
    assert params["crf"] == "18"


def test_build_trim_command_draft_uses_ultrafast(tmp_path: Path) -> None:
    source = tmp_path / "src.mp4"
    source.touch()
    dest = tmp_path / "out.mp4"

    monkeypatched_info = {
        "width": 1920,
        "height": 1080,
        "fps": 30.0,
        "fps_str": "30/1",
        "pix_fmt": "yuv420p",
    }
    text_edit._VIDEO_PROBE_CACHE[source] = monkeypatched_info
    try:
        cmd = text_edit.build_trim_command(source, 0.0, 1.0, dest, quality="draft")
    finally:
        text_edit._VIDEO_PROBE_CACHE.pop(source, None)

    assert "-preset" in cmd
    preset_idx = cmd.index("-preset")
    assert cmd[preset_idx + 1] == "ultrafast"
    crf_idx = cmd.index("-crf")
    assert cmd[crf_idx + 1] == "28"


def test_build_trim_command_final_uses_medium(tmp_path: Path) -> None:
    source = tmp_path / "src.mp4"
    source.touch()
    dest = tmp_path / "out.mp4"

    text_edit._VIDEO_PROBE_CACHE[source] = {
        "width": 1920,
        "height": 1080,
        "fps": 30.0,
        "fps_str": "30/1",
        "pix_fmt": "yuv420p",
    }
    try:
        cmd = text_edit.build_trim_command(source, 0.0, 1.0, dest, quality="final")
    finally:
        text_edit._VIDEO_PROBE_CACHE.pop(source, None)

    assert "-preset" in cmd
    assert cmd[cmd.index("-preset") + 1] == "medium"
    assert cmd[cmd.index("-crf") + 1] == "18"


def _make_simple_manifest(source_file: str) -> dict[str, Any]:
    return {
        "version": 1,
        "sources": [{"id": "clip01", "file": source_file}],
        "segments": [
            {
                "id": "clip01-s0001",
                "source": "clip01",
                "start": 0.0,
                "end": 0.5,
                "text": "hello",
            },
            {
                "id": "clip01-s0002",
                "source": "clip01",
                "start": 0.5,
                "end": 1.0,
                "text": "world",
            },
        ],
    }


def test_cache_populated_on_first_render(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    source = tmp_path / "sample.mp4"
    source.write_bytes(b"fake-video-data")
    cache_dir = tmp_path / ".eve-cache"
    encode_calls: list[str] = []

    def fake_probe(_path: Path) -> dict[str, Any]:
        return {
            "width": 320,
            "height": 240,
            "fps": 30.0,
            "fps_str": "30/1",
            "pix_fmt": "yuv420p",
        }

    def fake_run_ffmpeg(cmd: list[str], *, context: str | None = None) -> None:
        out = Path(cmd[-1])
        out.write_text("clip", encoding="utf-8")
        encode_calls.append(out.name)

    monkeypatch.setattr(text_edit, "probe_video_characteristics", fake_probe)
    monkeypatch.setattr(text_edit, "run_ffmpeg", fake_run_ffmpeg)

    manifest = _make_simple_manifest(str(source))
    text_edit.render_segments(manifest, tmp_path, tmp_path, cache_dir=cache_dir)

    segments_dir = cache_dir / "segments"
    cached_files = list(segments_dir.glob("*.mp4"))
    assert len(cached_files) == 2
    assert len(encode_calls) == 2


def test_cache_hit_skips_re_encode(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    source = tmp_path / "sample.mp4"
    source.write_bytes(b"fake-video-data")
    cache_dir = tmp_path / ".eve-cache"
    encode_calls: list[str] = []

    def fake_probe(_path: Path) -> dict[str, Any]:
        return {
            "width": 320,
            "height": 240,
            "fps": 30.0,
            "fps_str": "30/1",
            "pix_fmt": "yuv420p",
        }

    def fake_run_ffmpeg(cmd: list[str], *, context: str | None = None) -> None:
        out = Path(cmd[-1])
        out.write_text("clip", encoding="utf-8")
        encode_calls.append(out.name)

    monkeypatch.setattr(text_edit, "probe_video_characteristics", fake_probe)
    monkeypatch.setattr(text_edit, "run_ffmpeg", fake_run_ffmpeg)

    manifest = _make_simple_manifest(str(source))

    text_edit.render_segments(manifest, tmp_path, tmp_path, cache_dir=cache_dir)
    assert len(encode_calls) == 2

    encode_calls.clear()

    text_edit.render_segments(manifest, tmp_path, tmp_path, cache_dir=cache_dir)
    assert len(encode_calls) == 0, (
        "Expected zero encodes on re-render with unchanged manifest"
    )


def test_cache_re_encodes_only_edited_segment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    source = tmp_path / "sample.mp4"
    source.write_bytes(b"fake-video-data")
    cache_dir = tmp_path / ".eve-cache"
    encode_calls: list[str] = []

    def fake_probe(_path: Path) -> dict[str, Any]:
        return {
            "width": 320,
            "height": 240,
            "fps": 30.0,
            "fps_str": "30/1",
            "pix_fmt": "yuv420p",
        }

    def fake_run_ffmpeg(cmd: list[str], *, context: str | None = None) -> None:
        out = Path(cmd[-1])
        out.write_text("clip", encoding="utf-8")
        encode_calls.append(out.name)

    monkeypatch.setattr(text_edit, "probe_video_characteristics", fake_probe)
    monkeypatch.setattr(text_edit, "run_ffmpeg", fake_run_ffmpeg)

    manifest = _make_simple_manifest(str(source))
    text_edit.render_segments(manifest, tmp_path, tmp_path, cache_dir=cache_dir)
    assert len(encode_calls) == 2

    encode_calls.clear()

    manifest["segments"][0] = {
        "id": "clip01-s0001",
        "source": "clip01",
        "start": 0.0,
        "end": 0.4,
        "text": "hello",
    }

    text_edit.render_segments(manifest, tmp_path, tmp_path, cache_dir=cache_dir)

    assert len(encode_calls) == 1, (
        f"Expected 1 encode (only the trimmed segment), got {len(encode_calls)}"
    )


def test_validate_arg_in_parser() -> None:
    parser = build_parser()
    args = parser.parse_args(
        ["text-edit", "edit.tjm.json", "--output", "out.mp4", "--validate"]
    )
    assert args.validate is True


def test_validate_reports_missing_source(tmp_path: Path) -> None:
    manifest = {
        "version": 1,
        "sources": [{"id": "clip01", "file": str(tmp_path / "missing.mp4")}],
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
    errors, warnings = text_edit.validate_manifest_for_render(manifest, tmp_path)
    assert any("missing.mp4" in e for e in errors)


def test_validate_reports_missing_broll(tmp_path: Path) -> None:
    source = tmp_path / "sample.mp4"
    source.touch()
    manifest = {
        "version": 1,
        "sources": [{"id": "clip01", "file": str(source)}],
        "segments": [
            {
                "id": "clip01-s0001",
                "source": "clip01",
                "start": 0.0,
                "end": 0.5,
                "edit": {"broll": {"file": str(tmp_path / "missing_broll.mp4")}},
            }
        ],
    }
    errors, _warnings = text_edit.validate_manifest_for_render(manifest, tmp_path)
    assert any("missing_broll.mp4" in e for e in errors)


def test_validate_clean_manifest_produces_no_errors(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    source = tmp_path / "sample.mp4"
    source.write_bytes(b"fake-video-data")

    def fake_probe(_path: Path) -> dict[str, Any]:
        return {
            "width": 1920,
            "height": 1080,
            "fps": 30.0,
            "fps_str": "30/1",
            "pix_fmt": "yuv420p",
        }

    monkeypatch.setattr(text_edit, "probe_video_characteristics", fake_probe)

    manifest = _make_simple_manifest(str(source))
    errors, warnings = text_edit.validate_manifest_for_render(manifest, tmp_path)
    assert errors == []


def test_validate_detects_non_positive_duration(tmp_path: Path) -> None:
    source = tmp_path / "sample.mp4"
    source.write_bytes(b"fake")
    manifest = {
        "version": 1,
        "sources": [{"id": "clip01", "file": str(source)}],
        "segments": [
            {
                "id": "clip01-s0001",
                "source": "clip01",
                "start": 1.0,
                "end": 0.5,
                "text": "backwards",
            }
        ],
    }
    errors, _warnings = text_edit.validate_manifest_for_render(manifest, tmp_path)
    assert any("non-positive duration" in e for e in errors)


def test_codec_arg_defaults_to_none() -> None:
    parser = build_parser()
    args = parser.parse_args(["text-edit", "edit.tjm.json", "--output", "out.mp4"])
    assert args.codec is None


def test_codec_arg_accepts_mjpeg() -> None:
    parser = build_parser()
    args = parser.parse_args(
        ["text-edit", "edit.tjm.json", "--output", "out.mp4", "--codec", "mjpeg"]
    )
    assert args.codec == "mjpeg"


def test_encoding_args_draft_h264() -> None:
    args = text_edit.encoding_args("draft", "h264")
    assert "-c:v" in args
    assert "libx264" in args
    assert "ultrafast" in args


def test_encoding_args_draft_mjpeg() -> None:
    args = text_edit.encoding_args("draft", "mjpeg")
    assert "mjpeg" in args
    assert "-q:v" in args


def test_encoding_args_final_ignores_mjpeg() -> None:
    args_mjpeg = text_edit.encoding_args("final", "mjpeg")
    args_h264 = text_edit.encoding_args("final", "h264")
    assert args_mjpeg == args_h264
    assert "libx264" in args_h264


def test_effective_pix_fmt_mjpeg_draft() -> None:
    result = text_edit.effective_pix_fmt("yuv420p", "mjpeg", "draft")
    assert result == "yuvj420p"


def test_effective_pix_fmt_h264_unchanged() -> None:
    result = text_edit.effective_pix_fmt("yuv420p", "h264", "draft")
    assert result == "yuv420p"


def test_read_meta_missing_cache_dir(tmp_path: Path) -> None:
    cache_dir = tmp_path / "no-cache"
    cache_dir.mkdir()
    meta = text_edit._read_meta(cache_dir)
    assert meta == {}


def test_update_timing_writes_meta(tmp_path: Path) -> None:
    cache_dir = tmp_path / ".eve-cache"
    cache_dir.mkdir()
    text_edit._update_timing(cache_dir, "draft", elapsed=4.0, count=2)
    meta = text_edit._read_meta(cache_dir)
    avg = meta["avg_seconds_per_segment"]["draft"]
    assert avg == pytest.approx(2.0, rel=0.01)
    assert meta["sample_count"]["draft"] == 2


def test_update_timing_accumulates(tmp_path: Path) -> None:
    cache_dir = tmp_path / ".eve-cache"
    cache_dir.mkdir()
    text_edit._update_timing(cache_dir, "draft", elapsed=4.0, count=2)
    text_edit._update_timing(cache_dir, "draft", elapsed=6.0, count=3)
    meta = text_edit._read_meta(cache_dir)
    avg = meta["avg_seconds_per_segment"]["draft"]
    assert avg == pytest.approx(2.0, rel=0.1)


def test_dry_run_arg_in_parser() -> None:
    parser = build_parser()
    args = parser.parse_args(
        ["text-edit", "edit.tjm.json", "--output", "out.mp4", "--dry-run"]
    )
    assert args.dry_run is True


def test_analyze_render_all_cache_misses(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    source = tmp_path / "sample.mp4"
    source.write_bytes(b"fake-video-data")
    cache_dir = tmp_path / ".eve-cache"
    cache_dir.mkdir()

    manifest = _make_simple_manifest(str(source))
    analysis = text_edit.analyze_render(
        manifest, quality="draft", scale=1.0, cache_dir=cache_dir
    )

    assert analysis["total_segments"] == 2
    assert analysis["cached_segments"] == 0
    assert analysis["changed_segments"] == 2
    assert len(analysis["changed_ids"]) == 2


def test_analyze_render_after_warm_cache(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    source = tmp_path / "sample.mp4"
    source.write_bytes(b"fake-video-data")
    cache_dir = tmp_path / ".eve-cache"

    def fake_probe(_path: Path) -> dict[str, Any]:
        return {
            "width": 320,
            "height": 240,
            "fps": 30.0,
            "fps_str": "30/1",
            "pix_fmt": "yuv420p",
        }

    def fake_run_ffmpeg(cmd: list[str], *, context: str | None = None) -> None:
        Path(cmd[-1]).write_text("clip", encoding="utf-8")

    monkeypatch.setattr(text_edit, "probe_video_characteristics", fake_probe)
    monkeypatch.setattr(text_edit, "run_ffmpeg", fake_run_ffmpeg)

    manifest = _make_simple_manifest(str(source))
    text_edit.render_segments(
        manifest, tmp_path, tmp_path, cache_dir=cache_dir, quality="draft"
    )

    analysis = text_edit.analyze_render(
        manifest, quality="draft", scale=1.0, cache_dir=cache_dir
    )

    assert analysis["total_segments"] == 2
    assert analysis["cached_segments"] == 2
    assert analysis["changed_segments"] == 0
    assert analysis["cache_hit_rate"] == 1.0


def test_checkpoint_written_during_render(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    source = tmp_path / "sample.mp4"
    source.write_bytes(b"fake-video-data")
    cache_dir = tmp_path / ".eve-cache"

    def fake_probe(_path: Path) -> dict[str, Any]:
        return {
            "width": 320,
            "height": 240,
            "fps": 30.0,
            "fps_str": "30/1",
            "pix_fmt": "yuv420p",
        }

    def fake_run_ffmpeg(cmd: list[str], *, context: str | None = None) -> None:
        Path(cmd[-1]).write_text("clip", encoding="utf-8")

    monkeypatch.setattr(text_edit, "probe_video_characteristics", fake_probe)
    monkeypatch.setattr(text_edit, "run_ffmpeg", fake_run_ffmpeg)

    manifest = _make_simple_manifest(str(source))
    text_edit.render_segments(
        manifest, tmp_path, tmp_path, cache_dir=cache_dir, quality="draft", resume=True
    )

    checkpoint = text_edit._read_checkpoint(cache_dir)
    assert len(checkpoint.get("completed", [])) == 2


def test_no_resume_flag_in_parser() -> None:
    parser = build_parser()
    args = parser.parse_args(
        ["text-edit", "edit.tjm.json", "--output", "out.mp4", "--no-resume"]
    )
    assert args.no_resume is True


def test_segments_arg_in_parser() -> None:
    parser = build_parser()
    args = parser.parse_args(
        [
            "text-edit",
            "edit.tjm.json",
            "--output",
            "out.mp4",
            "--segments",
            "clip01-s0001",
            "clip01-s0002",
        ]
    )
    assert args.segments == ["clip01-s0001", "clip01-s0002"]


def test_partial_every_arg_in_parser() -> None:
    parser = build_parser()
    args = parser.parse_args(
        ["text-edit", "edit.tjm.json", "--output", "out.mp4", "--partial-every", "10"]
    )
    assert args.partial_every == 10
