"""Manifest-owned subtitle styles and frame-synchronized ASS burning."""

from __future__ import annotations

import json
import math
import pathlib
import re
import shutil
import subprocess
import tempfile
from bisect import bisect_left
from dataclasses import dataclass
from fractions import Fraction
from typing import Any


class SubtitleError(ValueError):
    """A subtitle request cannot be honored without changing its meaning."""


@dataclass(frozen=True)
class TimedToken:
    text: str
    intervals: list[tuple[Fraction, Fraction]]


@dataclass(frozen=True)
class TimedCaption:
    start: Fraction
    end: Fraction
    tokens: list[TimedToken]
    prefix: str = ""


def validate_configuration(value: Any) -> None:
    def obj(item: Any, path: str) -> dict[str, Any]:
        if not isinstance(item, dict):
            raise SubtitleError(f"{path} must be an object")
        return item

    def number(item: Any, path: str, *, positive: bool = False) -> None:
        if (
            isinstance(item, bool)
            or not isinstance(item, (int, float))
            or not math.isfinite(item)
            or (item <= 0 if positive else item < 0)
        ):
            raise SubtitleError(
                f"{path} must be a finite "
                f"{'positive' if positive else 'non-negative'} number"
            )

    def color(item: Any, path: str) -> None:
        if not isinstance(item, str) or not re.fullmatch(r"#[0-9a-fA-F]{6}", item):
            raise SubtitleError(f"{path} must be #RRGGBB")

    config = obj(value, "subtitles")
    if config.get("mode") != "burn":
        raise SubtitleError("subtitles.mode must be 'burn'")
    if config.get("style") not in ("plain", "karaoke"):
        raise SubtitleError("subtitles.style must be 'plain' or 'karaoke'")
    font = obj(config.get("font"), "subtitles.font")
    family = font.get("family")
    if (
        not isinstance(family, str)
        or not family.strip()
        or any(c in family for c in "\r\n,\x00")
    ):
        raise SubtitleError(
            "subtitles.font.family must be non-empty, "
            "without control characters or commas"
        )
    number(font.get("size"), "subtitles.font.size", positive=True)
    if not isinstance(font.get("bold"), bool):
        raise SubtitleError("subtitles.font.bold must be boolean")
    placement = obj(config.get("placement"), "subtitles.placement")
    if placement.get("anchor") != "bottom-center":
        raise SubtitleError("subtitles.placement.anchor must be 'bottom-center'")
    number(placement.get("bottom_margin"), "subtitles.placement.bottom_margin")
    outline = obj(config.get("outline"), "subtitles.outline")
    number(outline.get("width"), "subtitles.outline.width")
    color(outline.get("color"), "subtitles.outline.color")
    color(config.get("color"), "subtitles.color")
    if config["style"] == "karaoke" or "highlight_color" in config:
        color(config.get("highlight_color"), "subtitles.highlight_color")
    if "reference_canvas" in config:
        canvas = obj(config["reference_canvas"], "subtitles.reference_canvas")
        for dimension in ("width", "height"):
            size = canvas.get(dimension)
            if isinstance(size, bool) or not isinstance(size, int) or size <= 0:
                raise SubtitleError(
                    f"subtitles.reference_canvas.{dimension} must be a positive integer"
                )


def resolve_font(family: str, bold: bool) -> pathlib.Path:
    """Resolve an exact installed family/weight, never accept fontconfig fallback."""
    if shutil.which("fc-match") is None:
        raise SubtitleError("Subtitle font lookup requires fontconfig (fc-match)")
    style = "Bold" if bold else "Regular"
    result = subprocess.run(
        ["fc-match", "-f", "%{family}\t%{style}\t%{file}", f"{family}:style={style}"],
        check=True,
        capture_output=True,
        text=True,
    )
    fields = result.stdout.split("\t")
    if len(fields) == 3:
        families, actual_style, file = fields
        matches_family = family.casefold() in {
            name.strip().casefold() for name in families.split(",")
        }
        matches_weight = ("bold" in actual_style.casefold()) == bold
        path = pathlib.Path(file)
        if matches_family and matches_weight and path.is_file():
            return path
    raise SubtitleError(
        f"Subtitle font '{family}' ({style}) is unavailable; install the requested face"
    )


def _ass_color(rgb: str) -> str:
    return f"&H00{rgb[5:7]}{rgb[3:5]}{rgb[1:3]}"


def _ass_timestamp(seconds: float) -> str:
    centiseconds = max(0, round(seconds * 100))
    hours, rest = divmod(centiseconds, 360000)
    minutes, rest = divmod(rest, 6000)
    seconds_int, fraction = divmod(rest, 100)
    return f"{hours}:{minutes:02}:{seconds_int:02}.{fraction:02}"


def _ass_text(text: str) -> str:
    # libass accepts escaped braces. Separate literal user backslashes from
    # their following character so sequences such as \\N cannot execute tags.
    return (
        text.replace("\\", "\\\u2060")
        .replace("{", r"\{")
        .replace("}", r"\}")
        .replace("\r", "")
        .replace("\n", r"\N")
    )


def probe_frame_times(video: pathlib.Path) -> list[Fraction]:
    """Read presentation timestamps rather than infer a CFR grid from metadata."""
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "frame=best_effort_timestamp:stream=time_base",
            "-of",
            "json",
            str(video),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    data = json.loads(result.stdout)
    base = Fraction(data["streams"][0]["time_base"])
    times = [int(frame["best_effort_timestamp"]) * base for frame in data["frames"]]
    if not times or times != sorted(set(times)):
        raise SubtitleError("Highlighting requires ordered, distinct frame timestamps")
    return times


def _frame_millisecond(
    time: Fraction, rate: Fraction, frame_times: list[Fraction] | None = None
) -> int:
    """Choose the first output frame at/after a boundary, then libass's clock.

    FFmpeg passes frame PTS to libass in integer milliseconds. Quantizing to
    centiseconds first can select the wrong frame, notably at 60/120 fps.
    """
    if frame_times is not None:
        index = bisect_left(frame_times, time)
        if index == len(frame_times):
            return max(math.ceil(time * 1000), int(frame_times[-1] * 1000) + 1)
        chosen = int(frame_times[index] * 1000)
        if index > 0 and int(frame_times[index - 1] * 1000) == chosen:
            raise SubtitleError(
                "A token boundary separates frames within the same libass millisecond; "
                "cannot highlight those frames precisely"
            )
        return chosen
    return int(Fraction(math.ceil(time * rate), 1) / rate * 1000)


def _timed_events(
    captions: list[TimedCaption],
    config: dict[str, Any],
    rate: Fraction,
    frame_times: list[Fraction] | None = None,
) -> str:
    active = _ass_color(config.get("highlight_color", config["color"])) + "&"
    base = _ass_color(config["color"]) + "&"
    events: list[str] = []
    for caption in captions:
        # The ASS event is only a coarse container. Millisecond step transforms
        # choose the exact output frames for caption visibility and token color.
        container_start = Fraction(math.floor(caption.start * 100), 100)
        container_end = Fraction(math.ceil(caption.end * 100), 100)
        origin_ms = int(container_start * 1000)
        visible_start = _frame_millisecond(caption.start, rate, frame_times) - origin_ms
        visible_end = _frame_millisecond(caption.end, rate, frame_times) - origin_ms
        if visible_end <= visible_start:
            continue  # No output frame intersects this caption.
        visibility = (
            r"\alpha&H00&"
            if visible_start == 0
            else (rf"\alpha&HFF&\t({visible_start},{visible_start},\alpha&H00&)")
        )
        visibility += rf"\t({visible_end},{visible_end},\alpha&HFF&)"
        parts: list[str] = []
        for token in caption.tokens:
            color = rf"\1c{base}"
            for start, end in token.intervals:
                first = _frame_millisecond(start, rate, frame_times) - origin_ms
                last = _frame_millisecond(end, rate, frame_times) - origin_ms
                if last <= first:
                    continue
                if first <= 0:
                    color = rf"\1c{active}"
                else:
                    color += rf"\t({first},{first},\1c{active})"
                color += rf"\t({last},{last},\1c{base})"
            parts.append("{" + color + "}" + _ass_text(token.text))
        text = "{" + visibility + "}" + _ass_text(caption.prefix) + " ".join(parts)
        events.append(
            f"Dialogue: 0,{_ass_timestamp(float(container_start))},"
            f"{_ass_timestamp(float(container_end))},Default,,0,0,0,,{text}\n"
        )
    return "".join(events)


def ass_document(
    cues: list[tuple[float, float, str]],
    config: dict[str, Any],
    width: int,
    height: int,
    *,
    timed_captions: list[TimedCaption] | None = None,
    frame_rate: str | None = None,
    frame_times: list[Fraction] | None = None,
) -> str:
    validate_configuration(config)
    if width <= 0 or height <= 0:
        raise SubtitleError("Subtitle burning requires positive output dimensions")
    canvas = config.get("reference_canvas", {"width": 1920, "height": 1080})
    scale = min(width / canvas["width"], height / canvas["height"])
    font = config["font"]
    outline = config["outline"]
    margin = config["placement"]["bottom_margin"] * scale
    header = (
        "[Script Info]\nScriptType: v4.00+\n"
        f"PlayResX: {width}\nPlayResY: {height}\n"
        "WrapStyle: 0\nScaledBorderAndShadow: yes\n\n[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, "
        "ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, "
        "MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Default,{font['family']},{font['size'] * scale:g},"
        f"{_ass_color(config['color'])},{_ass_color(config['color'])},"
        f"{_ass_color(outline['color'])},&H00000000,{(-1 if font['bold'] else 0)},"
        f"0,0,0,100,100,0,0,1,{outline['width'] * scale:g},0,2,"
        f"{margin:g},{margin:g},{margin:g},1\n\n[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, "
        "Effect, Text\n"
    )
    if config["style"] == "karaoke" or frame_times is not None:
        if config["style"] == "karaoke" and timed_captions is None:
            raise SubtitleError("Karaoke requires timed tokens")
        if frame_times is None and frame_rate is None:
            raise SubtitleError(
                "Timed captions require frame timestamps or a frame rate"
            )
        rate = Fraction(frame_rate or "1")
        if rate <= 0 or (rate > 1000 and frame_times is None):
            raise SubtitleError("Timed captions require a representable frame clock")
        if timed_captions is None:
            timed_captions = [
                TimedCaption(
                    Fraction(str(start)), Fraction(str(end)), [TimedToken(text, [])]
                )
                for start, end, text in cues
                if text and end > start
            ]
        return header + _timed_events(timed_captions, config, rate, frame_times)
    return header + "".join(
        f"Dialogue: 0,{_ass_timestamp(start)},{_ass_timestamp(end)},"
        f"Default,,0,0,0,,{_ass_text(text)}\n"
        for start, end, text in cues
        if text and end > start
    )


def burn_video(
    video: pathlib.Path,
    cues: list[tuple[float, float, str]],
    config: dict[str, Any],
    width: int,
    height: int,
    encoding: list[str],
    font_file: pathlib.Path,
    *,
    timed_captions: list[TimedCaption] | None = None,
    frame_rate: str | None = None,
) -> None:
    """Re-encode only the final video; preserve audio and replace on success."""
    frame_times = probe_frame_times(video)
    script = ass_document(
        cues,
        config,
        width,
        height,
        timed_captions=timed_captions,
        frame_rate=frame_rate,
        frame_times=frame_times,
    )
    with tempfile.TemporaryDirectory(prefix="eve-subtitles-", dir=video.parent) as temp:
        working = pathlib.Path(temp)
        (working / "captions.ass").write_text(script, encoding="utf-8")
        fonts = working / "fonts"
        fonts.mkdir()
        shutil.copyfile(font_file, fonts / ("caption-font" + font_file.suffix))
        output = working / "burned.mp4"
        # Static relative filter paths avoid FFmpeg's multiple escaping layers,
        # even when the video or parent directory contains quotes or punctuation.
        command = [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-copyts",
            "-i",
            str(video.resolve()),
            "-map",
            "0:v:0",
            "-map",
            "0:a?",
            "-vf",
            "ass=filename=captions.ass:fontsdir=fonts",
            *encoding,
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "copy",
            "-fps_mode",
            "passthrough",
            "-enc_time_base",
            "demux",
            "-movie_timescale",
            "1000000",
            str(output.resolve()),
        ]
        try:
            subprocess.run(
                command, cwd=working, check=True, capture_output=True, text=True
            )
        except subprocess.CalledProcessError as exc:
            raise SubtitleError(f"Subtitle burning failed: {exc.stderr}") from exc
        output.replace(video)
