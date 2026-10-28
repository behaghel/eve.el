from __future__ import annotations

import copy
import json
import shutil
import subprocess
from fractions import Fraction
from pathlib import Path
from typing import Any

import pytest

from eve_cli import subtitles, tjm_v11
from eve_cli.commands import text_edit


def configuration() -> dict[str, Any]:
    return {
        "mode": "burn",
        "style": "plain",
        "font": {"family": "Arial", "size": 48, "bold": True},
        "placement": {"anchor": "bottom-center", "bottom_margin": 60},
        "color": "#FFFFFF",
        "outline": {"color": "#000000", "width": 3},
    }


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("mode", "other"),
        ("style", "other"),
        ("color", "white"),
        ("font", {"family": "", "size": 48, "bold": True}),
        ("font", {"family": "Arial", "size": True, "bold": True}),
        ("font", {"family": "Arial", "size": float("nan"), "bold": True}),
        ("outline", {"color": "#000000", "width": -1}),
        ("placement", {"anchor": "top", "bottom_margin": 60}),
        ("reference_canvas", {"width": 1920.5, "height": 1080}),
        ("reference_canvas", {"width": True, "height": 1080}),
    ],
)
def test_invalid_subtitle_configuration_is_rejected(field: str, value: Any) -> None:
    config = configuration()
    config[field] = value
    with pytest.raises(ValueError, match="subtitles"):
        subtitles.validate_configuration(config)


def test_plain_ass_has_one_event_per_segment_and_scaled_style() -> None:
    config = configuration()
    original = copy.deepcopy(config)
    script = subtitles.ass_document(
        [(0.0, 1.0, "First caption"), (1.5, 2.5, "Second caption")],
        config,
        960,
        540,
    )
    assert "PlayResX: 960" in script
    assert "PlayResY: 540" in script
    assert "Style: Default,Arial,24," in script
    assert "&H00FFFFFF" in script
    assert ",1.5," in script  # Outline scales from 3 reference pixels.
    assert ",30," in script  # Bottom margin scales from 60 reference pixels.
    events = [line for line in script.splitlines() if line.startswith("Dialogue:")]
    assert len(events) == 2
    assert "0:00:00.00,0:00:01.00" in events[0]
    assert "0:00:01.50,0:00:02.50" in events[1]
    assert config == original


def test_caption_text_cannot_inject_ass_overrides() -> None:
    script = subtitles.ass_document(
        [(0, 1, r"literal {\p1} and \N")], configuration(), 1920, 1080
    )
    event = next(line for line in script.splitlines() if line.startswith("Dialogue:"))
    assert r"{\p1}" not in event
    assert r"\N" not in event


@pytest.mark.skipif(shutil.which("fc-match") is None, reason="fontconfig unavailable")
def test_missing_font_is_an_error() -> None:
    with pytest.raises(ValueError, match="font.*unavailable"):
        subtitles.resolve_font("Eve Deliberately Missing Font 12345", True)


def test_token_highlights_preserve_rewrites_and_pauses(tmp_path: Path) -> None:
    manifest = {
        "version": "1.1",
        "sources": [
            {
                "id": "s",
                "file": "clip.mp4",
                "timebase": {"numerator": 1, "denominator": 1000},
            }
        ],
        "render": {"filler_policy": "keep", "preserve_short_gaps": 0},
        "segments": [
            {
                "id": "one",
                "source": "s",
                "start_tick": 0,
                "end_tick": 1000,
                "display_text": "just two now",
                "words": [
                    {
                        "spoken": "twenty-five",
                        "display": "just two",
                        "start_tick": 103,
                        "end_tick": 247,
                    },
                    {"spoken": "now", "start_tick": 351, "end_tick": 449},
                ],
            }
        ],
    }
    prepared = text_edit.prepare_render_manifest(manifest, tmp_path)
    captions = text_edit.build_token_captions(prepared)
    assert len(captions) == 1
    caption = captions[0]
    assert (caption.start, caption.end) == (0, 1)
    assert [token.text for token in caption.tokens] == ["just two", "now"]
    assert caption.tokens[0].intervals == [(Fraction(103, 1000), Fraction(247, 1000))]
    assert caption.tokens[1].intervals == [(Fraction(351, 1000), Fraction(449, 1000))]
    config = configuration() | {"style": "karaoke", "highlight_color": "#FFD700"}
    script = subtitles.ass_document(
        [(0, 1, "just two now")],
        config,
        1920,
        1080,
        timed_captions=captions,
        frame_rate="60/1",
    )
    # 103 ms belongs to frame 7 (116 ms), not frame 6 (100 ms).
    # ASS event timestamps alone only have centisecond precision.
    assert r"\t(116,116,\1c" in script
    assert r"\t(250,250,\1c" in script
    assert r"\t(366,366,\1c" in script
    assert "just two" in script
    assert script.count("Dialogue:") == 1


def test_highlights_use_actual_variable_frame_timestamps() -> None:
    config = configuration() | {"style": "karaoke", "highlight_color": "#FFD700"}
    caption = subtitles.TimedCaption(
        Fraction(0),
        Fraction(1, 10),
        [
            subtitles.TimedToken("word", [(Fraction(3, 100), Fraction(5, 100))]),
        ],
    )
    script = subtitles.ass_document(
        [],
        config,
        1920,
        1080,
        timed_captions=[caption],
        frame_rate="60/1",
        frame_times=[Fraction(n, 100) for n in (0, 2, 4, 6, 8)],
    )
    assert r"\t(40,40,\1c" in script
    assert r"\t(60,60,\1c" in script
    assert r"\t(33,33,\1c" not in script


def test_unrepresentable_frame_boundaries_fail_instead_of_guessing() -> None:
    config = configuration() | {"style": "karaoke", "highlight_color": "#FFD700"}
    caption = subtitles.TimedCaption(
        Fraction(0),
        Fraction(1),
        [
            subtitles.TimedToken("word", [(Fraction(10001, 100000), Fraction(1, 5))]),
        ],
    )
    with pytest.raises(ValueError, match="same libass millisecond"):
        subtitles.ass_document(
            [],
            config,
            1920,
            1080,
            timed_captions=[caption],
            frame_rate="60/1",
            frame_times=[
                Fraction(0),
                Fraction(1, 10),
                Fraction(10002, 100000),
                Fraction(1),
            ],
        )


@pytest.mark.parametrize("style", ["plain", "karaoke"])
def test_legacy_v1_burning_is_rejected_before_rendering(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    style: str,
) -> None:
    from eve_cli.main import main

    path = tmp_path / "legacy.tjm.json"
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "sources": [],
                "segments": [],
                "subtitles": configuration()
                | {"style": style, "highlight_color": "#FFD700"},
            }
        )
    )
    assert main(["text-edit", str(path), "--output", str(tmp_path / "out.mp4")]) == 1
    error = capsys.readouterr().err
    assert "v1.1" in error
    assert "v1" in error
    assert "Traceback" not in error
    assert not (tmp_path / "out.mp4").exists()


def test_bad_token_is_a_cli_diagnostic_not_a_python_traceback(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from eve_cli.main import main

    manifest = {
        "version": "1.1",
        "sources": [
            {
                "id": "s",
                "file": "clip.mp4",
                "timebase": {"numerator": 1, "denominator": 1000},
            }
        ],
        "render": {"filler_policy": "keep", "preserve_short_gaps": 0},
        "segments": [
            {
                "id": "clip01-s0067",
                "source": "s",
                "start_tick": 181410,
                "end_tick": 184150,
                "words": [{"spoken": "it.", "start_tick": 184150, "end_tick": 184150}],
            }
        ],
        "subtitles": configuration()
        | {"style": "karaoke", "highlight_color": "#FFD700"},
    }
    path = tmp_path / "edit.tjm.json"
    path.write_text(json.dumps(manifest))
    assert main(["text-edit", str(path), "--output", str(tmp_path / "out.mp4")]) == 1
    error = capsys.readouterr().err
    assert "clip01-s0067" in error
    assert "it." in error
    assert "184150" in error
    assert "Traceback" not in error
    assert not (tmp_path / "out.mp4").exists()


def test_validation_error_keeps_the_json_cli_contract(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from eve_cli.main import main

    path = tmp_path / "invalid.tjm.json"
    path.write_text(
        json.dumps(
            {
                "version": "1.1",
                "sources": [],
                "segments": [],
                "render": {"filler_policy": "keep", "preserve_short_gaps": 0},
                "subtitles": {"mode": "invalid"},
            }
        )
    )
    assert (
        main(
            [
                "text-edit",
                str(path),
                "--output",
                str(tmp_path / "unused.mp4"),
                "--validate",
                "--json",
            ]
        )
        == 1
    )
    output = capsys.readouterr()
    diagnostic = json.loads(output.out)
    assert diagnostic["valid"] is False
    assert "subtitles.mode" in diagnostic["errors"][0]["message"]
    assert output.err == ""


def test_karaoke_requires_timed_tokens_without_guessing() -> None:
    with pytest.raises(ValueError, match="timed words"):
        text_edit.build_token_captions(
            {
                "segments": [
                    {
                        "id": "untimed",
                        "source": "s",
                        "start": 0,
                        "end": 1,
                        "text": "hello",
                    }
                ]
            }
        )
    with pytest.raises(ValueError, match="display_text"):
        text_edit.build_token_captions(
            {
                "segments": [
                    {
                        "id": "unaligned",
                        "source": "s",
                        "start": 0,
                        "end": 1,
                        "display_text": "unbound text",
                        "words": [{"token": "hello", "start": 0, "end": 1}],
                    }
                ]
            }
        )


def test_token_timeline_tracks_deletions_gaps_and_renderable_markers() -> None:
    def word(text: str, start: float, end: float, **edit: Any) -> dict[str, Any]:
        return {"token": text, "start": start, "end": end, "edit": edit}

    manifest = {
        "segments": [
            {
                "id": "edited",
                "source": "s",
                "start": 1,
                "end": 2,
                "words": [
                    word("keep", 1, 1.25),
                    word("drop", 1.25, 1.5, deleted=True),
                    word("also", 1.75, 2),
                ],
            },
            {
                "id": "deleted",
                "source": "s",
                "start": 2,
                "end": 2.5,
                "edit": {"deleted": True},
                "words": [word("gone", 2, 2.5)],
            },
            {
                "id": "next",
                "source": "s",
                "start": 2.75,
                "end": 3.25,
                "words": [word("next", 2.75, 3.25)],
            },
            {
                "id": "marker",
                "kind": "marker",
                "edit": {"broll": {"file": "title.mp4", "duration": 0.5}},
            },
            {
                "id": "last",
                "source": "s",
                "start": 3.5,
                "end": 4,
                "words": [word("last", 3.5, 4)],
            },
        ]
    }
    captions = text_edit.build_token_captions(manifest, preserve_gap_threshold=0.5)
    assert [(caption.start, caption.end) for caption in captions] == [
        (0, Fraction(1, 2)),
        (Fraction(3, 4), Fraction(5, 4)),
        (Fraction(7, 4), Fraction(9, 4)),
    ]
    assert [token.text for token in captions[0].tokens] == ["keep", "also"]
    assert captions[0].tokens[1].intervals == [(Fraction(1, 4), Fraction(1, 2))]
    assert captions[2].tokens[0].intervals == [(Fraction(7, 4), Fraction(9, 4))]


@pytest.mark.skipif(
    not Path("/System/Library/Fonts/Supplemental/Arial Bold.ttf").is_file(),
    reason="macOS system Arial Bold is not installed",
)
def test_macos_system_font_is_discoverable_without_substitution() -> None:
    path = subtitles.resolve_font("Arial", True)
    assert path.is_file()
    assert "Arial" in path.name


def test_v11_subtitle_validation_is_not_silently_ignored() -> None:
    manifest = {
        "version": "1.1",
        "sources": [],
        "segments": [],
        "render": {"filler_policy": "keep", "preserve_short_gaps": 0},
        "subtitles": {"mode": "burn"},
    }
    with pytest.raises(tjm_v11.ValidationError, match="subtitles"):
        tjm_v11.parse_manifest(manifest)


@pytest.mark.media
@pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("fc-match") is None,
    reason="FFmpeg and fontconfig are required for the real render test",
)
@pytest.mark.parametrize("style", ["plain", "karaoke"])
def test_burn_in_from_tjm_ticks(tmp_path: Path, style: str) -> None:
    # Select a known text face for the fixture, not a renderer fallback. A
    # generic fontconfig alias can resolve to an icon-only face on macOS/Nix.
    for family in ("DejaVu Sans", "Arial", "Inter", "Liberation Sans"):
        try:
            subtitles.resolve_font(family, True)
            break
        except subtitles.SubtitleError:
            continue
    else:
        pytest.skip("Install a test font: DejaVu Sans, Arial, Inter or Liberation Sans")
    config = configuration()
    config["font"] = {"family": family, "size": 48, "bold": True}
    config["style"] = style
    config["highlight_color"] = "#FFD700"
    source = tmp_path / "source.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=0x203040:size=1920x1080:rate=60:duration=2",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=2",
            "-c:v",
            "libx264",
            "-c:a",
            "aac",
            "-shortest",
            str(source),
        ],
        check=True,
    )
    manifest: dict[str, Any] = {
        "version": "1.1",
        "sources": [
            {
                "id": "source",
                "file": "source.mp4",
                "timebase": {"numerator": 1, "denominator": 1000},
            }
        ],
        "segments": [
            {
                "id": "first",
                "source": "source",
                "start_tick": 0,
                "end_tick": 500,
                "display_text": "FIRST TOKEN NOW",
                "words": [
                    {
                        "spoken": "first",
                        "display": "FIRST TOKEN",
                        "start_tick": 103,
                        "end_tick": 247,
                    },
                    {"spoken": "NOW", "start_tick": 351, "end_tick": 449},
                ],
                "start": 99,
                "end": 100,
            },
            {
                "id": "second",
                "source": "source",
                "start_tick": 1000,
                "end_tick": 1500,
                "display_text": "SECOND",
                "words": [{"spoken": "SECOND", "start_tick": 1000, "end_tick": 1500}],
            },
        ],
        "render": {"filler_policy": "keep", "preserve_short_gaps": 0.5},
        "subtitles": config,
    }
    path = tmp_path / "edit.tjm.json"
    path.write_text(json.dumps(manifest))
    output = tmp_path / "final.mp4"
    # Exercise actual assembly before burning: AAC padding must not move video
    # PTS or add samples at every cut. Keep a workdir to inspect intermediate clips.
    from eve_cli.main import main

    assembled_manifest = copy.deepcopy(manifest)
    assembled_manifest.pop("subtitles")
    assembled_path = tmp_path / "assembled.tjm.json"
    assembled_path.write_text(json.dumps(assembled_manifest))
    assembled_output = tmp_path / "assembled.mp4"
    workdir = tmp_path / "work"
    workdir.mkdir()
    assert (
        main(
            [
                "text-edit",
                str(assembled_path),
                "--output",
                str(assembled_output),
                "--quality",
                "final",
                "--no-cache",
                "--workdir",
                str(workdir),
            ]
        )
        == 0
    )

    def stream_info(video: Path) -> dict[str, dict[str, Any]]:
        data = json.loads(
            subprocess.check_output(
                [
                    "ffprobe",
                    "-v",
                    "error",
                    "-show_streams",
                    "-of",
                    "json",
                    str(video),
                ]
            )
        )
        return {stream["codec_type"]: stream for stream in data["streams"]}

    assembled_streams = stream_info(assembled_output)
    assert Fraction(assembled_streams["video"]["start_time"]) == 0
    assert Fraction(assembled_streams["audio"]["start_time"]) == 0
    audio = assembled_streams["audio"]
    assert abs(Fraction(audio["duration"]) - Fraction(3, 2)) <= Fraction(
        1, int(audio["sample_rate"])
    )

    assert (
        main(
            [
                "text-edit",
                str(path),
                "--output",
                str(output),
                "--quality",
                "final",
                "--no-cache",
            ]
        )
        == 0
    )

    def bright_pixels(at: float) -> int:
        frame = subprocess.check_output(
            [
                "ffmpeg",
                "-v",
                "error",
                "-ss",
                str(at),
                "-i",
                str(output),
                "-frames:v",
                "1",
                "-pix_fmt",
                "gray",
                "-f",
                "rawvideo",
                "-",
            ]
        )
        assert len(frame) == 1920 * 1080
        return sum(value > 150 for value in frame)

    assert bright_pixels(0.25) > 20
    assert bright_pixels(0.75) == 0  # Caption absent in the preserved gap.
    assert bright_pixels(1.25) > 20
    info = text_edit.probe_video_characteristics(output)
    assert (info["width"], info["height"]) == (1920, 1080)
    burned_streams = stream_info(output)
    assert (
        burned_streams["video"]["start_time"]
        == assembled_streams["video"]["start_time"]
    )
    assert (
        burned_streams["audio"]["duration_ts"]
        == assembled_streams["audio"]["duration_ts"]
    )
    assert json.loads(path.read_text()) == manifest
    if style == "karaoke":

        def yellow_pixels(frame_index: int) -> int:
            pixels = subprocess.check_output(
                [
                    "ffmpeg",
                    "-v",
                    "error",
                    "-i",
                    str(output),
                    "-vf",
                    f"select=eq(n\\,{frame_index}),crop=1000:180:460:900",
                    "-frames:v",
                    "1",
                    "-pix_fmt",
                    "rgb24",
                    "-f",
                    "rawvideo",
                    "-",
                ]
            )
            assert len(pixels) == 1000 * 180 * 3
            return sum(
                r > 150 and g > 100 and b < 80
                for r, g, b in zip(pixels[::3], pixels[1::3], pixels[2::3])
            )

        # Check real encoded frames either side of non-centisecond boundaries.
        for frame_index in (6, 15, 21, 27, 45):
            assert yellow_pixels(frame_index) == 0, frame_index
        for frame_index in (7, 14, 22, 26, 60):
            assert yellow_pixels(frame_index) > 50, frame_index


def test_v11_render_adapter_preserves_original_token_and_uses_ticks(
    tmp_path: Path,
) -> None:
    manifest: dict[str, Any] = {
        "version": "1.1",
        "sources": [
            {
                "id": "s",
                "file": "clip.mp4",
                "timebase": {"numerator": 1, "denominator": 48000},
            }
        ],
        "segments": [
            {
                "id": "one",
                "source": "s",
                "start_tick": 48000,
                "end_tick": 96000,
                "words": [
                    {
                        "spoken": "twenty-five",
                        "display": "just two",
                        "start_tick": 48000,
                        "end_tick": 96000,
                        "start": 99,
                        "end": 100,
                    }
                ],
            }
        ],
        "render": {"filler_policy": "keep", "preserve_short_gaps": 0},
    }
    before = copy.deepcopy(manifest)
    rendered = text_edit.prepare_render_manifest(manifest, tmp_path)
    segment = rendered["segments"][0]
    assert len(segment["words"]) == 1
    assert segment["words"][0]["token"] == "just two"
    assert segment["text"] == "just two"
    assert segment["words"][0]["spoken"] == "twenty-five"
    assert (segment["start"], segment["end"]) == (1, 2)
    assert (segment["words"][0]["start"], segment["words"][0]["end"]) == (1, 2)
    assert rendered["sources"][0]["file"] == str(tmp_path / "clip.mp4")
    assert manifest == before


def test_v11_render_adapter_accepts_empty_editor_metadata(tmp_path: Path) -> None:
    manifest = {
        "version": "1.1",
        "sources": [
            {
                "id": "s",
                "file": "clip.mp4",
                "timebase": {"numerator": 1, "denominator": 1000},
            }
        ],
        "segments": [
            {
                "id": "one",
                "source": "s",
                "start_tick": 0,
                "end_tick": 1000,
                "words": [
                    {"spoken": "hello", "start_tick": 0, "end_tick": 1000, "edit": None}
                ],
            }
        ],
        "render": {"filler_policy": "keep", "preserve_short_gaps": 0},
    }
    rendered = text_edit.prepare_render_manifest(manifest, tmp_path)
    assert rendered["segments"][0]["words"][0]["token"] == "hello"
    assert manifest["segments"][0]["words"][0]["edit"] is None


def test_burn_failure_preserves_video(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    video = tmp_path / "video.mp4"
    video.write_bytes(b"original video")
    font = tmp_path / "font.ttf"
    font.write_bytes(b"test font")

    def fail(*args: Any, **kwargs: Any) -> None:
        raise subprocess.CalledProcessError(1, "ffmpeg", stderr="libass unavailable")

    monkeypatch.setattr(subtitles, "probe_frame_times", lambda _: [Fraction(0)])
    monkeypatch.setattr(subtitles.subprocess, "run", fail)
    with pytest.raises(ValueError, match="Subtitle burning failed.*libass"):
        subtitles.burn_video(
            video,
            [(0, 1, "hello")],
            configuration(),
            320,
            180,
            ["-c:v", "libx264"],
            font,
        )
    assert video.read_bytes() == b"original video"
    assert not list(tmp_path.glob("eve-subtitles-*"))
