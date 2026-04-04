from __future__ import annotations

import hashlib
import json
import os
import pathlib
import shutil
import subprocess
import tempfile
import time
import uuid
from argparse import ArgumentParser, Namespace, _SubParsersAction
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from .common import add_json_flag

FFMPEG = "ffmpeg"
FFPROBE = "ffprobe"

QUALITY_DRAFT = "draft"
QUALITY_FINAL = "final"

CODEC_H264 = "h264"
CODEC_MJPEG = "mjpeg"

_VIDEO_PROBE_CACHE: dict[pathlib.Path, dict[str, Any]] = {}


def encoding_params(quality: str, codec: str = CODEC_H264) -> dict[str, str]:
    if quality == QUALITY_DRAFT:
        return {"preset": "ultrafast", "crf": "28"}
    return {"preset": "medium", "crf": "18"}


def encoding_args(quality: str, codec: str = CODEC_H264) -> list[str]:
    effective = codec if quality == QUALITY_DRAFT else CODEC_H264
    if effective == CODEC_MJPEG:
        return ["-c:v", "mjpeg", "-q:v", "5"]
    params = encoding_params(quality)
    return ["-c:v", "libx264", "-preset", params["preset"], "-crf", params["crf"]]


def effective_pix_fmt(probed: str, codec: str, quality: str) -> str:
    if codec == CODEC_MJPEG and quality == QUALITY_DRAFT:
        return "yuvj420p"
    return probed


def _make_scale_vf(scale: float) -> str | None:
    if scale >= 1.0:
        return None
    return f"scale=trunc(iw*{scale}/2)*2:trunc(ih*{scale}/2)*2"


def _file_identity(path: pathlib.Path) -> str:
    st = path.stat()
    return f"{path}:{st.st_size}:{st.st_mtime_ns}"


def segment_cache_key(item: dict[str, Any], quality: str) -> str:
    h = hashlib.sha256()
    h.update(quality.encode())

    if item["type"] == "gap":
        source_path: pathlib.Path = item["source_path"]
        h.update(_file_identity(source_path).encode())
        h.update(b"gap")
        h.update(str(item["gap_bounds"]).encode())
        return h.hexdigest()

    source_path = item["source_path"]
    if source_path is not None:
        h.update(_file_identity(source_path).encode())
    else:
        h.update(b"no-source")

    seg = item["segment"]
    h.update(str(seg.get("start_tick", seg.get("start"))).encode())
    h.update(str(seg.get("end_tick", seg.get("end"))).encode())
    h.update(json.dumps(normalized_edit_state(seg), sort_keys=True).encode())
    for word in seg.get("words", []):
        h.update(json.dumps(normalized_edit_state(word), sort_keys=True).encode())

    if item.get("key"):
        chain_meta = item.get("chain_meta") or {}
        h.update(str(item["key"]).encode())
        h.update(str(chain_meta.get("base_offset", 0.0)).encode())
        h.update(str(item["segment_info"].get("offset", 0.0)).encode())
        h.update(str(item["overlay_duration"]).encode())

    return h.hexdigest()


def register(subparsers: _SubParsersAction[ArgumentParser]) -> None:
    parser = subparsers.add_parser(
        "text-edit",
        help="Render an edited timeline from a TJM manifest.",
    )
    parser.add_argument("manifest", help="Path to the TJM JSON manifest")
    parser.add_argument("--output", required=True, help="Output MP4 file")
    parser.add_argument(
        "--workdir",
        help="Optional directory to place intermediate files",
    )
    parser.add_argument(
        "--pretty-manifest",
        help="Write an updated manifest reflecting the rendered cut",
    )
    parser.add_argument(
        "--preserve-short-gaps",
        type=float,
        metavar="SECONDS",
        help="Insert original footage for intra-source gaps shorter than SECONDS",
    )
    parser.add_argument(
        "--subtitles",
        nargs="?",
        const="",
        help=(
            "Generate WebVTT subtitles. Optionally provide a path; "
            "defaults to <output>.vtt when omitted."
        ),
    )
    parser.add_argument(
        "--no-subtitle-mux",
        action="store_true",
        help="Do not mux the generated subtitles into the output container",
    )
    parser.add_argument(
        "--quality",
        choices=[QUALITY_DRAFT, QUALITY_FINAL],
        default=QUALITY_DRAFT,
        help=(
            "Encoding quality: 'draft' (ultrafast/crf28, fast preview) "
            "or 'final' (medium/crf18, delivery quality)."
        ),
    )
    parser.add_argument(
        "--jobs",
        type=int,
        default=0,
        metavar="N",
        help=(
            "Number of segments to encode in parallel. "
            "0 (default) auto-detects CPU count, capped at 8."
        ),
    )
    parser.add_argument(
        "--codec",
        choices=[CODEC_H264, CODEC_MJPEG],
        default=None,
        help=(
            "Override intermediate codec (default: mjpeg for draft, h264 for final). "
            "Overridden to h264 when --quality final."
        ),
    )
    parser.add_argument(
        "--validate",
        action="store_true",
        help=(
            "Validate the manifest without rendering. "
            "Exits 0 if valid, 1 if errors are found. "
            "Combine with --json for machine-readable output."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Analyse which segments would be re-rendered without encoding anything. "
            "Reports cache hit rate and an estimated render time. "
            "Use with --json for machine-readable output."
        ),
    )
    parser.add_argument(
        "--scale",
        type=float,
        default=None,
        metavar="FACTOR",
        help=(
            "Scale video dimensions by FACTOR (e.g. 0.5 for half resolution). "
            "Only effective with --quality draft; ignored for final renders."
        ),
    )
    parser.add_argument(
        "--cache-dir",
        help=(
            "Directory for the persistent segment cache. "
            "Defaults to .eve-cache/ next to the manifest."
        ),
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="Disable segment cache; re-encode every segment from scratch.",
    )
    parser.add_argument(
        "--partial-every",
        type=int,
        default=0,
        metavar="N",
        help=(
            "Write a partial <output>.partial.mp4 after every N segments complete. "
            "Emits a JSON event on stdout for each partial file. "
            "0 (default) disables progressive output."
        ),
    )
    parser.add_argument(
        "--segments",
        nargs="+",
        metavar="SEGMENT_ID",
        help=(
            "Render only the specified segment IDs (by manifest id field), "
            "writing results to the cache without producing a final output. "
            "Intended for background speculative pre-rendering."
        ),
    )
    parser.add_argument(
        "--no-resume",
        action="store_true",
        help=(
            "Disable checkpoint resume; always render all segments from scratch "
            "even if a prior interrupted render left a checkpoint."
        ),
    )
    parser.add_argument(
        "--cache-max-size",
        type=float,
        default=10.0,
        metavar="GB",
        help="Maximum cache size in gigabytes before LRU eviction (default: 10).",
    )
    add_json_flag(parser)
    parser.set_defaults(handler=run, command="text-edit")


def probe_video_characteristics(path: pathlib.Path) -> dict[str, Any]:
    cached = _VIDEO_PROBE_CACHE.get(path)
    if cached is not None:
        return cached

    cmd = [
        FFPROBE,
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=width,height,r_frame_rate,pix_fmt",
        "-of",
        "json",
        str(path),
    ]
    result = subprocess.run(cmd, check=True, capture_output=True, text=True)
    data = json.loads(result.stdout)
    streams = data.get("streams") or []
    if not streams:
        raise RuntimeError(f"Unable to probe video characteristics for {path}")
    stream = streams[0]
    width = int(stream.get("width") or 0)
    height = int(stream.get("height") or 0)
    fps_str = stream.get("r_frame_rate") or "30/1"
    try:
        num, denom = fps_str.split("/")
        fps = float(num) / float(denom)
    except Exception:
        fps = 30.0
        fps_str = "30/1"
    pix_fmt = stream.get("pix_fmt") or "yuv420p"

    info = {
        "width": width,
        "height": height,
        "fps": fps,
        "fps_str": fps_str,
        "pix_fmt": pix_fmt,
    }
    _VIDEO_PROBE_CACHE[path] = info
    return info


def load_manifest(path: pathlib.Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as file_handle:
        data = json.load(file_handle)
    if not isinstance(data, dict):
        raise ValueError(f"Manifest '{path}' must decode to a JSON object")
    return data


def ensure_inputs(paths: list[pathlib.Path]) -> None:
    for path in paths:
        if not path.exists():
            raise FileNotFoundError(f"Input file '{path}' not found")


def run_ffmpeg(cmd: list[str], *, context: str | None = None) -> None:
    try:
        subprocess.run(
            cmd,
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as exc:
        details = exc.stderr or exc.stdout or ""
        message = "ffmpeg command failed"
        if context:
            message += f" while {context}"
        if details:
            message += f":\n{details.strip()}"
        raise RuntimeError(message) from exc


def compute_gap(
    previous: dict[str, Any], current: dict[str, Any]
) -> tuple[str, float, float] | None:
    prev_source = previous.get("source")
    curr_source = current.get("source")
    if not isinstance(prev_source, str) or prev_source != curr_source:
        return None
    previous_bounds = segment_gap_bounds(previous)
    current_bounds = segment_gap_bounds(current)
    if previous_bounds is None or current_bounds is None:
        return None
    _, prev_end = previous_bounds
    curr_start, _ = current_bounds
    gap = curr_start - prev_end
    if gap <= 0:
        return None
    return prev_source, prev_end, curr_start


def parse_timecode(value: Any, *, default: float = 0.0) -> float:
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return default
        parts = text.split(":")
        if len(parts) == 1:
            try:
                return float(parts[0])
            except ValueError as exc:
                raise ValueError(f"Invalid time value '{value}'") from exc
        if len(parts) > 3:
            raise ValueError(f"Invalid time value '{value}'")
        try:
            numeric_parts = [float(part) for part in parts]
        except ValueError as exc:
            raise ValueError(f"Invalid time value '{value}'") from exc
        seconds = 0.0
        for index, segment in enumerate(reversed(numeric_parts)):
            seconds += segment * (60**index)
        return seconds
    raise TypeError(f"Unsupported time value type: {type(value)!r}")


def segment_kind(segment: dict[str, Any]) -> str:
    return str(segment.get("kind") or "segment").lower()


def segment_filename(index: int) -> str:
    return f"segment_{index:04d}.mp4"


def normalized_edit_state(item: Any) -> dict[str, Any]:
    if not isinstance(item, dict):
        return {}
    edit = item.get("edit")
    return edit if isinstance(edit, dict) else {}


def normalized_edit_value(item: Any, field: str) -> Any:
    if not isinstance(item, dict):
        return None
    edit = normalized_edit_state(item)
    if field in edit:
        return edit[field]
    return item.get(field)


def normalized_segment_broll(segment: dict[str, Any]) -> dict[str, Any] | None:
    broll = normalized_edit_value(segment, "broll")
    return broll if isinstance(broll, dict) else None


def edit_deleted(item: Any) -> bool:
    return bool(normalized_edit_state(item).get("deleted"))


def deleted_marker(segment: dict[str, Any]) -> bool:
    return segment_kind(segment) == "marker" and edit_deleted(segment)


def segment_words(segment: dict[str, Any]) -> list[dict[str, Any]]:
    words = segment.get("words")
    if not isinstance(words, list):
        return []
    return [word for word in words if isinstance(word, dict)]


def surviving_words(segment: dict[str, Any]) -> list[dict[str, Any]]:
    return [word for word in segment_words(segment) if not edit_deleted(word)]


def has_deleted_words(segment: dict[str, Any]) -> bool:
    return any(edit_deleted(word) for word in segment_words(segment))


def raw_segment_bounds(segment: dict[str, Any]) -> tuple[float, float] | None:
    start = segment.get("start")
    end = segment.get("end")
    if start is None or end is None:
        return None
    try:
        start_value = float(start)
        end_value = float(end)
    except Exception:
        return None
    if end_value <= start_value:
        return None
    return start_value, end_value


def merge_ranges(ranges: list[tuple[float, float]]) -> list[tuple[float, float]]:
    merged: list[tuple[float, float]] = []
    for start, end in sorted(ranges):
        if not merged or start > merged[-1][1]:
            merged.append((start, end))
            continue
        merged[-1] = (merged[-1][0], max(merged[-1][1], end))
    return merged


def surviving_word_ranges(segment: dict[str, Any]) -> list[tuple[float, float]]:
    if not has_deleted_words(segment):
        return []
    bounds = raw_segment_bounds(segment)
    if bounds is None:
        return []
    segment_start, segment_end = bounds
    ranges: list[tuple[float, float]] = []
    for word in surviving_words(segment):
        word_start_value = word.get("start")
        word_end_value = word.get("end")
        if word_start_value is None or word_end_value is None:
            continue
        try:
            word_start = max(segment_start, float(word_start_value))
            word_end = min(segment_end, float(word_end_value))
        except Exception:
            continue
        if word_end > word_start:
            ranges.append((word_start, word_end))
    return merge_ranges(ranges)


def segment_media_ranges(segment: dict[str, Any]) -> list[tuple[float, float]]:
    if has_deleted_words(segment):
        return surviving_word_ranges(segment)
    bounds = raw_segment_bounds(segment)
    if bounds is None:
        return []
    return [bounds]


def segment_gap_bounds(segment: dict[str, Any]) -> tuple[float, float] | None:
    if edit_deleted(segment):
        return raw_segment_bounds(segment)
    ranges = surviving_word_ranges(segment)
    if ranges:
        return ranges[0][0], ranges[-1][1]
    return raw_segment_bounds(segment)


def render_word_text(segment: dict[str, Any]) -> str:
    return " ".join(
        str(word.get("token", "")) for word in surviving_words(segment)
    ).strip()


def format_timestamp(seconds: float) -> str:
    total_ms = max(0, int(round(seconds * 1000)))
    hours, remainder = divmod(total_ms, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, ms = divmod(remainder, 1_000)
    return f"{hours:02}:{minutes:02}:{secs:02}.{ms:03}"


def format_minsec(seconds: float) -> str:
    total_seconds = max(0, int(round(seconds)))
    minutes, secs = divmod(total_seconds, 60)
    return f"{minutes:02}:{secs:02}"


def cue_text(segment: dict[str, Any]) -> str:
    uses_word_text = has_deleted_words(segment)
    if uses_word_text:
        text = render_word_text(segment)
    else:
        text = str(segment.get("text") or "").strip()
        if not text:
            text = render_word_text(segment)
    speaker = str(segment.get("speaker") or "").strip()
    if speaker and text:
        return f"{speaker}: {text}"
    if uses_word_text:
        return text
    return text or str(segment.get("id") or "")


def canonical_broll_key(segment: dict[str, Any]) -> tuple[Any, ...] | None:
    broll = normalized_segment_broll(segment)
    if broll is None or not broll.get("file"):
        return None
    file_path = str(pathlib.Path(str(broll["file"])).expanduser())
    mode = str(broll.get("mode") or "replace").lower()
    audio_policy = str(broll.get("audio") or "source").lower()
    still = bool(broll.get("still"))
    position = tuple(sorted((broll.get("position") or {}).items()))
    template_flag = file_path.lower().endswith(".json")
    return (file_path, mode, audio_policy, still, position, template_flag)


def escape_drawtext_value(value: str) -> str:
    return value.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")


def build_drawtext_filters(
    overlays: list[dict[str, Any]] | None, placeholders: dict[str, str]
) -> str:
    filters: list[str] = []
    for overlay in overlays or []:
        placeholder = overlay.get("placeholder")
        if not placeholder or placeholder not in placeholders:
            continue
        text_value = escape_drawtext_value(str(placeholders[placeholder]))
        parts: list[str] = [f"text='{text_value}'"]

        font = overlay.get("font") or overlay.get("fontfile")
        if font:
            font_path = pathlib.Path(str(font)).expanduser()
            parts.append(f"fontfile='{escape_drawtext_value(str(font_path))}'")

        fontsize = overlay.get("fontsize") or overlay.get("size")
        if fontsize:
            parts.append(f"fontsize={fontsize}")

        color = overlay.get("color") or overlay.get("fontColor")
        if color:
            parts.append(f"fontcolor={color}")

        x_expr = overlay.get("x") or overlay.get("position_x")
        y_expr = overlay.get("y") or overlay.get("position_y")
        align = overlay.get("align") or overlay.get("alignment")
        if align == "center" and not x_expr:
            x_expr = "(w-text_w)/2"
        if align == "center" and not y_expr:
            y_expr = "(h-text_h)/2"
        parts.append(f"x={x_expr or 0}")
        parts.append(f"y={y_expr or 0}")

        box_color = overlay.get("boxColor") or overlay.get("box_color")
        if box_color:
            parts.append("box=1")
            parts.append(f"boxcolor={box_color}")

        shadow_color = overlay.get("shadowColor") or overlay.get("shadow_color")
        if shadow_color:
            parts.append(f"shadowcolor={shadow_color}")
        shadow_x = overlay.get("shadow_x") or overlay.get("shadowX")
        if shadow_x is not None:
            parts.append(f"shadowx={shadow_x}")
        shadow_y = overlay.get("shadow_y") or overlay.get("shadowY")
        if shadow_y is not None:
            parts.append(f"shadowy={shadow_y}")

        start_time = overlay.get("start")
        end_time = overlay.get("end")
        duration = overlay.get("duration")
        if start_time is not None:
            start_val = float(start_time)
            if duration is not None:
                end_val = start_val + float(duration)
            elif end_time is not None:
                end_val = float(end_time)
            else:
                end_val = start_val
            parts.append(f"enable='between(t,{start_val},{end_val})'")

        filters.append("drawtext=" + ":".join(parts))

    return ",".join(filters)


def load_broll_spec(broll: dict[str, Any]) -> dict[str, Any]:
    file_value = broll.get("file")
    if not file_value:
        raise ValueError("B-roll entry missing 'file' attribute")
    file_path = pathlib.Path(str(file_value)).expanduser()

    spec: dict[str, Any] = {
        "media_path": file_path,
        "overlays": [],
        "placeholders": {},
    }

    if file_path.suffix.lower() == ".json":
        with file_path.open("r", encoding="utf-8") as file_handle:
            template_spec = json.load(file_handle)
        if not isinstance(template_spec, dict):
            raise ValueError(f"Template JSON '{file_path}' must decode to an object")
        template_path = template_spec.get("template")
        if not template_path:
            raise ValueError(f"Template JSON '{file_path}' missing 'template' field")
        spec["media_path"] = pathlib.Path(str(template_path)).expanduser()
        spec["overlays"] = template_spec.get("overlays") or []
        spec["placeholders"] = template_spec.get("placeholders") or {}

    if broll.get("overlays"):
        spec["overlays"] = broll["overlays"]

    placeholders: dict[str, Any] = {}
    placeholders.update(spec.get("placeholders", {}))
    placeholders.update(broll.get("placeholders") or {})
    spec["placeholders"] = {key: str(value) for key, value in placeholders.items()}

    return spec


def prepare_broll_media(
    broll: dict[str, Any],
    source_info: dict[str, Any] | None,
    total_duration: float,
    working: pathlib.Path,
    audio_policy: str,
    quality: str = QUALITY_FINAL,
    scale: float = 1.0,
    codec: str = CODEC_H264,
) -> tuple[pathlib.Path, str]:
    _enc_args = encoding_args(quality, codec)
    _svf = _make_scale_vf(scale)
    spec = load_broll_spec(broll)
    media_path = pathlib.Path(spec["media_path"])
    overlays = spec.get("overlays") or []
    placeholders = spec.get("placeholders") or {}

    base_info = probe_video_characteristics(media_path)
    target_info = source_info or base_info

    target_width = int(target_info.get("width") or base_info.get("width") or 1920)
    target_height = int(target_info.get("height") or base_info.get("height") or 1080)
    target_width = max(2, target_width - (target_width % 2))
    target_height = max(2, target_height - (target_height % 2))

    raw_pix_fmt = str(
        target_info.get("pix_fmt") or base_info.get("pix_fmt") or "yuv420p"
    )
    out_pix_fmt = effective_pix_fmt(raw_pix_fmt, codec, quality)
    fps_str = str(target_info.get("fps_str") or base_info.get("fps_str") or "30/1")

    filters: list[str] = []
    base_width = int(base_info.get("width") or target_width)
    base_height = int(base_info.get("height") or target_height)
    if base_width != target_width or base_height != target_height:
        filters.append(
            f"scale={target_width}:{target_height}:force_original_aspect_ratio=increase"
        )
        filters.append(f"crop={target_width}:{target_height}")
    base_fps = base_info.get("fps_str")
    if base_fps and base_fps != fps_str:
        filters.append(f"fps={fps_str}")
    filters.append(f"format={out_pix_fmt}")

    draw_filters = build_drawtext_filters(overlays, placeholders)
    if draw_filters:
        filters.append(draw_filters)
    if _svf:
        filters.append(_svf)

    filter_chain = ",".join(filters) if filters else None

    prepared_path = working / f"broll_prepared_{uuid.uuid4().hex}.mp4"
    cmd: list[str] = [FFMPEG, "-y"]

    is_still = bool(broll.get("still")) or media_path.suffix.lower() in {
        ".png",
        ".jpg",
        ".jpeg",
        ".bmp",
        ".gif",
    }
    total_needed = max(total_duration, 0.033)

    if is_still:
        cmd.extend(["-loop", "1", "-i", str(media_path), "-t", f"{total_needed:.3f}"])
        if filter_chain:
            cmd.extend(["-vf", filter_chain])
        cmd.extend(_enc_args)
        cmd.extend(["-pix_fmt", out_pix_fmt, "-an", str(prepared_path)])
    else:
        cmd.extend(["-i", str(media_path)])
        if total_duration > 0:
            cmd.extend(["-t", f"{total_needed:.3f}"])
        if filter_chain:
            cmd.extend(["-vf", filter_chain])
        cmd.extend(_enc_args)
        cmd.extend(["-pix_fmt", out_pix_fmt])
        if audio_policy == "broll":
            cmd.extend(["-c:a", "aac"])
        else:
            cmd.extend(["-an"])
        cmd.append(str(prepared_path))

    run_ffmpeg(cmd, context=f"preparing b-roll media from {media_path}")
    return prepared_path, out_pix_fmt


def build_subtitle_cues(
    manifest: dict[str, Any], preserve_gap_threshold: float | None = None
) -> list[tuple[float, float, str]]:
    cues: list[tuple[float, float, str]] = []
    timeline = 0.0
    previous_source_segment: dict[str, Any] | None = None
    for segment in manifest.get("segments", []):
        if segment_kind(segment) == "marker":
            continue
        if preserve_gap_threshold is not None and previous_source_segment is not None:
            gap = compute_gap(previous_source_segment, segment)
            if gap is not None:
                _, gap_start, gap_end = gap
                gap_duration = gap_end - gap_start
                if gap_duration > 0 and gap_duration <= preserve_gap_threshold:
                    timeline += gap_duration
        duration = segment_duration(segment)
        if duration <= 0:
            continue
        previous_source_segment = segment
        if edit_deleted(segment):
            continue
        text = cue_text(segment)
        if not text:
            timeline += duration
            continue
        cue_start = timeline
        cue_end = cue_start + duration
        cues.append((cue_start, cue_end, text))
        timeline = cue_end
    return cues


def write_webvtt(cues: list[tuple[float, float, str]], path: pathlib.Path) -> None:
    if not cues:
        raise RuntimeError("No subtitle cues were generated from the manifest")
    path.parent.mkdir(parents=True, exist_ok=True)
    print(f"[eve text-edit] Writing WebVTT subtitles to {path}", flush=True)
    with path.open("w", encoding="utf-8") as file_handle:
        file_handle.write("WEBVTT\n\n")
        for index, (start, end, text) in enumerate(cues, start=1):
            file_handle.write(f"{index}\n")
            file_handle.write(
                f"{format_timestamp(start)} --> {format_timestamp(end)}\n"
            )
            file_handle.write(f"{text}\n\n")


def mux_subtitles(video_path: pathlib.Path, subtitles_path: pathlib.Path) -> None:
    with tempfile.NamedTemporaryFile(
        suffix=video_path.suffix,
        dir=str(video_path.parent),
        delete=False,
    ) as tmp_file:
        tmp_path = pathlib.Path(tmp_file.name)

    try:
        cmd = [
            FFMPEG,
            "-y",
            "-i",
            str(video_path),
            "-f",
            "webvtt",
            "-i",
            str(subtitles_path),
            "-map",
            "0",
            "-map",
            "-0:d",
            "-c:v",
            "copy",
            "-c:a",
            "copy",
            "-c:s",
            "mov_text",
            "-map",
            "1:0",
            str(tmp_path),
        ]
        run_ffmpeg(cmd, context="muxing subtitles into the final video")
        tmp_path.replace(video_path)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise


def build_trim_command(
    source: pathlib.Path,
    start: float,
    end: float,
    dest: pathlib.Path,
    quality: str = QUALITY_FINAL,
    scale: float = 1.0,
    codec: str = CODEC_H264,
) -> list[str]:
    info = probe_video_characteristics(source)
    raw_pix_fmt = str(info.get("pix_fmt") or "yuv420p")
    out_pix_fmt = effective_pix_fmt(raw_pix_fmt, codec, quality)
    _svf = _make_scale_vf(scale)
    cmd = [FFMPEG, "-y", "-ss", f"{start:.3f}", "-to", f"{end:.3f}", "-i", str(source)]
    if _svf:
        cmd.extend(["-vf", _svf])
    cmd.extend(encoding_args(quality, codec))
    cmd.extend(["-pix_fmt", out_pix_fmt, "-c:a", "aac", str(dest)])
    return cmd


def render_source_ranges(
    source: pathlib.Path,
    ranges: list[tuple[float, float]],
    dest: pathlib.Path,
    working: pathlib.Path,
    *,
    context: str,
    quality: str = QUALITY_FINAL,
    scale: float = 1.0,
    codec: str = CODEC_H264,
) -> None:
    if not ranges:
        raise RuntimeError(f"No surviving media ranges available while {context}")
    if len(ranges) == 1:
        start, end = ranges[0]
        cmd = build_trim_command(source, start, end, dest, quality, scale, codec)
        run_ffmpeg(cmd, context=context)
        return

    parts: list[pathlib.Path] = []
    for index, (start, end) in enumerate(ranges, start=1):
        part_path = working / f"word_range_{uuid.uuid4().hex}_{index:02d}.mp4"
        cmd = build_trim_command(source, start, end, part_path, quality, scale, codec)
        run_ffmpeg(cmd, context=f"{context} (part {index}/{len(ranges)})")
        parts.append(part_path)
    concat_segments(parts, dest)


def build_broll_command(
    source: pathlib.Path,
    start: float,
    end: float,
    broll: dict[str, Any],
    dest: pathlib.Path,
    working: pathlib.Path,
    *,
    effective_offset: float | None = None,
    effective_duration: float | None = None,
    quality: str = QUALITY_FINAL,
    scale: float = 1.0,
    codec: str = CODEC_H264,
) -> list[str]:
    broll_file = pathlib.Path(str(broll["file"])).expanduser()
    if not broll_file.exists():
        raise FileNotFoundError(f"B-roll file '{broll_file}' not found")

    mode = str(broll.get("mode") or "replace").lower()
    audio_policy = str(broll.get("audio") or "source").lower()
    if mode not in {"replace", "pip"}:
        raise ValueError(f"Unsupported b-roll mode '{mode}' (supported: replace, pip)")
    if audio_policy not in {"source", "broll"}:
        raise ValueError(
            f"Unsupported b-roll audio '{audio_policy}' (supported: source, broll)"
        )

    base_offset = parse_timecode(broll.get("start_offset"), default=0.0)
    broll_offset = effective_offset if effective_offset is not None else base_offset
    duration = (
        effective_duration
        if effective_duration is not None
        else parse_timecode(broll.get("duration"), default=0.0)
    )
    if duration <= 0:
        duration = max(0.0, end - start)

    still = bool(broll.get("still"))
    if still and audio_policy == "broll":
        raise ValueError(
            "Still-image b-roll cannot supply audio; set audio to 'source'"
        )

    _enc_args = encoding_args(quality, codec)
    _svf = _make_scale_vf(scale)
    source_info = probe_video_characteristics(source)
    target_width = max(2, int(source_info.get("width") or 1920))
    target_height = max(2, int(source_info.get("height") or 1080))
    if target_width % 2:
        target_width -= 1
    if target_height % 2:
        target_height -= 1
    raw_pix_fmt = str(source_info.get("pix_fmt") or "yuv420p")
    pix_fmt = effective_pix_fmt(raw_pix_fmt, codec, quality)
    fps_str = str(source_info.get("fps_str") or "30/1")

    prepared_broll = broll_file
    if still:
        prepared_broll = working / f"broll_still_{uuid.uuid4().hex}.mp4"
        scale_filter = (
            f"scale={target_width}:{target_height}:force_original_aspect_ratio=increase,"
            f"crop={target_width}:{target_height},"
            f"fps={fps_str},"
            f"format={pix_fmt}"
        )
        loop_cmd = [
            FFMPEG,
            "-y",
            "-loop",
            "1",
            "-i",
            str(broll_file),
            "-t",
            f"{duration:.3f}",
            "-vf",
            scale_filter,
            "-an",
            "-pix_fmt",
            pix_fmt,
            str(prepared_broll),
        ]
        run_ffmpeg(loop_cmd, context=f"preparing still b-roll from {broll_file}")
        broll_offset = 0.0

    if mode == "pip":
        pip_position = broll.get("position") or {}
        pos_x = float(pip_position.get("x", 0.05))
        pos_y = float(pip_position.get("y", 0.05))
        width = float(pip_position.get("width", 0.3))

        if width <= 0 or width >= 1:
            raise ValueError("pip width must be between 0 and 1 (exclusive)")
        if not (0 <= pos_x <= 1) or not (0 <= pos_y <= 1):
            raise ValueError("pip position x/y must be between 0 and 1 inclusive")

        scale_expr = f"iw*{width}:-1"
        overlay_expr = f"main_w*{pos_x}:main_h*{pos_y}"

        cmd: list[str] = [
            FFMPEG,
            "-y",
            "-ss",
            f"{start:.3f}",
            "-to",
            f"{end:.3f}",
            "-i",
            str(source),
        ]

        if still:
            cmd.extend(["-i", str(prepared_broll)])
        else:
            cmd.extend(
                [
                    "-ss",
                    f"{broll_offset:.3f}",
                    "-t",
                    f"{duration:.3f}",
                    "-i",
                    str(prepared_broll),
                ]
            )

        if _svf:
            filter_complex = (
                f"[1:v]scale={scale_expr}[pip];"
                f"[0:v][pip]overlay={overlay_expr}:eof_action=repeat[composited];"
                f"[composited]{_svf}[outv]"
            )
        else:
            filter_complex = (
                f"[1:v]scale={scale_expr}[pip];"
                f"[0:v][pip]overlay={overlay_expr}:eof_action=repeat[outv]"
            )

        audio_ch = "0:a:0" if audio_policy == "source" else "1:a:0"
        cmd.extend(
            ["-filter_complex", filter_complex, "-map", "[outv]", "-map", audio_ch]
        )
        cmd.extend(_enc_args)
        cmd.extend(["-pix_fmt", pix_fmt, "-c:a", "aac", str(dest)])
        return cmd

    audio_map = "0:a:0" if audio_policy == "source" else "1:a:0"

    cmd = [
        FFMPEG,
        "-y",
        "-ss",
        f"{start:.3f}",
        "-to",
        f"{end:.3f}",
        "-i",
        str(source),
    ]

    if still:
        cmd.extend(["-i", str(prepared_broll)])
    else:
        cmd.extend(
            [
                "-ss",
                f"{broll_offset:.3f}",
                "-t",
                f"{duration:.3f}",
                "-i",
                str(prepared_broll),
            ]
        )

    cmd.extend(["-map", "1:v:0", "-map", audio_map])
    if _svf:
        cmd.extend(["-vf", _svf])
    cmd.extend(_enc_args)
    cmd.extend(
        [
            "-pix_fmt",
            pix_fmt,
            "-c:a",
            "aac",
            str(dest),
        ]
    )

    return cmd


def segment_duration(segment: dict[str, Any]) -> float:
    if has_deleted_words(segment):
        return sum(end - start for start, end in segment_media_ranges(segment))

    bounds = raw_segment_bounds(segment)
    duration = 0.0
    if bounds is not None:
        duration = bounds[1] - bounds[0]
    if duration <= 0:
        duration = parse_timecode(segment.get("duration"), default=0.0)
    if duration <= 0:
        broll = normalized_segment_broll(segment)
        duration = parse_timecode((broll or {}).get("duration"), default=0.0)
    return max(0.0, duration)


def segment_overlay_duration(segment: dict[str, Any]) -> float:
    broll = normalized_segment_broll(segment)
    override = parse_timecode((broll or {}).get("duration"), default=0.0)
    if override > 0:
        return override
    return segment_duration(segment)


def compute_broll_chains(
    segments: list[dict[str, Any]],
) -> tuple[dict[int, dict[str, Any]], dict[int, dict[str, Any]]]:
    chain_map: dict[int, dict[str, Any]] = {}
    chains: dict[int, dict[str, Any]] = {}
    chain_counter = 0
    active_chain: dict[str, Any] | None = None

    for segment in segments:
        if deleted_marker(segment):
            continue
        if segment_kind(segment) != "marker" and edit_deleted(segment):
            continue
        key = canonical_broll_key(segment)
        seg_broll = normalized_segment_broll(segment) or {}
        overlay_duration = segment_overlay_duration(segment)
        duration = segment_duration(segment)
        continue_flag = bool(seg_broll.get("continue"))

        if key:
            if active_chain and active_chain["key"] == key and continue_flag:
                offset = active_chain["overlay_sum"]
                active_chain["overlay_sum"] += overlay_duration
                active_chain["total_duration"] = (
                    active_chain["base_offset"] + active_chain["overlay_sum"]
                )
            else:
                chain_counter += 1
                base_offset = parse_timecode(seg_broll.get("start_offset"), default=0.0)
                active_chain = {
                    "id": chain_counter,
                    "key": key,
                    "base_offset": base_offset,
                    "overlay_sum": overlay_duration,
                    "total_duration": base_offset + overlay_duration,
                }
                chains[chain_counter] = active_chain
                offset = 0.0
            chain_map[id(segment)] = {
                "chain_id": active_chain["id"],
                "offset": offset,
                "overlay_duration": overlay_duration,
                "duration": duration,
            }
            if not continue_flag:
                active_chain = None
        else:
            chain_map[id(segment)] = {
                "chain_id": None,
                "offset": 0.0,
                "overlay_duration": duration,
                "duration": duration,
            }
            active_chain = None

    return chain_map, chains


def render_segments(
    manifest: dict[str, Any],
    base_dir: pathlib.Path,
    working: pathlib.Path,
    preserve_gap_threshold: float | None = None,
    quality: str = QUALITY_FINAL,
    jobs: int = 0,
    cache_dir: pathlib.Path | None = None,
    scale: float = 1.0,
    codec: str = CODEC_H264,
    resume: bool = True,
) -> list[pathlib.Path]:
    id_to_source = {
        item["id"]: pathlib.Path(str(item["file"])).expanduser()
        for item in manifest.get("sources", [])
    }
    ensure_inputs(list(id_to_source.values()))

    manifest_segments = manifest.get("segments", [])
    chain_map, chains = compute_broll_chains(manifest_segments)

    def to_float(value: Any, default: float = 0.0) -> float:
        try:
            return float(value)
        except Exception:
            return default

    # ── Phase 1: Planning (serial, no ffmpeg) ────────────────────────────────
    # Walk the segment list exactly once to build an ordered list of work items
    # (gaps + segments).  Every item receives a pre-assigned output path so
    # the parallel render phase can run without coordinating clip_index.

    total_units = (
        sum(
            1
            for segment in manifest_segments
            if not deleted_marker(segment)
            if not (segment_kind(segment) != "marker" and edit_deleted(segment))
            if (segment_kind(segment) != "marker" or canonical_broll_key(segment))
            and (segment_duration(segment) > 0 or canonical_broll_key(segment))
        )
        or 1
    )

    work_items: list[dict[str, Any]] = []
    clip_index = 1
    rendered_count = 0
    previous_source_segment: dict[str, Any] | None = None

    for segment in manifest_segments:
        kind = segment_kind(segment)
        key = canonical_broll_key(segment)
        has_broll = bool(key)
        is_marker = kind == "marker"

        if deleted_marker(segment):
            continue

        if is_marker and not has_broll:
            continue

        source_id = segment.get("source")
        source_path = id_to_source.get(source_id) if source_id in id_to_source else None

        duration = segment_duration(segment)
        if duration <= 0 and not has_broll:
            if not is_marker:
                previous_source_segment = segment
            continue

        if not is_marker and edit_deleted(segment):
            previous_source_segment = segment
            continue

        start_val = to_float(segment.get("start"), 0.0)
        end_val = to_float(segment.get("end"), start_val + duration)

        segment_info = chain_map.get(
            id(segment),
            {
                "chain_id": None,
                "offset": 0.0,
                "overlay_duration": duration,
                "duration": duration,
            },
        )
        chain_id = segment_info.get("chain_id")
        chain_meta = chains.get(chain_id) if isinstance(chain_id, int) else None

        gap_duration = 0.0
        gap_bounds: tuple[str, float, float] | None = None
        prev_source_path: pathlib.Path | None = None
        if (
            preserve_gap_threshold is not None
            and previous_source_segment is not None
            and source_path is not None
        ):
            prev_source_id = previous_source_segment.get("source")
            if prev_source_id and prev_source_id in id_to_source:
                prev_source_path = id_to_source[prev_source_id]
                gap_info = compute_gap(previous_source_segment, segment)
                if gap_info is not None:
                    gap_bounds = gap_info
                    _, gap_start, gap_end = gap_info
                    gap_duration = max(0.0, gap_end - gap_start)

        skip_gap = False
        if (
            preserve_gap_threshold is not None
            and previous_source_segment is not None
            and key
            and (normalized_segment_broll(segment) or {}).get("continue")
        ):
            prev_key = canonical_broll_key(previous_source_segment)
            prev_chain = chain_map.get(id(previous_source_segment))
            if (
                prev_key == key
                and prev_chain
                and prev_chain.get("chain_id") == segment_info.get("chain_id")
            ):
                skip_gap = True

        if (
            preserve_gap_threshold is not None
            and gap_duration > 0
            and gap_duration <= preserve_gap_threshold
            and not skip_gap
            and gap_bounds is not None
            and prev_source_path is not None
        ):
            gap_path = working / segment_filename(clip_index)
            clip_index += 1
            work_items.append(
                {
                    "type": "gap",
                    "out_path": gap_path,
                    "source_path": prev_source_path,
                    "gap_bounds": gap_bounds,
                    "segment_id": segment.get("id"),
                    "gap_duration": gap_duration,
                }
            )

        out_path = working / segment_filename(clip_index)
        clip_index += 1
        rendered_count += 1

        broll = normalized_segment_broll(segment) or {}
        audio_policy = str(broll.get("audio") or "source").lower()
        if source_path is None and audio_policy == "source":
            audio_policy = "broll"

        overlay_duration = segment_info.get("overlay_duration", duration)
        description = (
            f"segment {segment.get('id') or rendered_count} "
            f"({(source_id or 'broll')} {start_val:.2f}s->{end_val:.2f}s)"
        )

        work_items.append(
            {
                "type": "segment",
                "out_path": out_path,
                "source_path": source_path,
                "source_id": source_id,
                "segment": segment,
                "start_val": start_val,
                "end_val": end_val,
                "description": description,
                "display_index": rendered_count,
                "key": key,
                "chain_meta": chain_meta,
                "segment_info": segment_info,
                "broll": broll,
                "audio_policy": audio_policy,
                "overlay_duration": overlay_duration,
                "duration": duration,
            }
        )

        previous_source_segment = segment

    if not work_items:
        raise RuntimeError("No segments rendered; manifest may be empty")

    # ── Phase 2: B-roll preparation (serial, one per chain) ──────────────────
    # Each b-roll chain is prepared exactly once before the parallel render.
    # chain_meta["prepared_path"] is the only shared write in the render loop;
    # doing it here makes all Phase 3 work items fully independent.

    for item in work_items:
        if item["type"] != "segment":
            continue
        chain_meta = item["chain_meta"]
        if chain_meta is None or "prepared_path" in chain_meta:
            continue
        if not item["key"]:
            continue
        source_info = (
            probe_video_characteristics(item["source_path"])
            if item["source_path"]
            else None
        )
        total_needed = max(
            chain_meta.get("total_duration", item["overlay_duration"]),
            item["overlay_duration"],
        )
        prepared_path, prepared_pix_fmt = prepare_broll_media(
            item["broll"],
            source_info,
            total_needed,
            working,
            item["audio_policy"],
            quality,
            scale,
            codec,
        )
        chain_meta["prepared_path"] = prepared_path
        chain_meta["prepared_pix_fmt"] = prepared_pix_fmt

    # ── Phase 3: Parallel rendering ──────────────────────────────────────────
    # All work items are now fully independent.  Render them in parallel using
    # threads (ffmpeg subprocesses release the GIL) then reassemble in order.
    # Cache hits are resolved before submission and never enter the executor.

    segments_dir: pathlib.Path | None = None
    if cache_dir is not None:
        segments_dir = cache_dir / "segments"
        segments_dir.mkdir(parents=True, exist_ok=True)

    def _cache_path(item: dict[str, Any]) -> pathlib.Path | None:
        if segments_dir is None:
            return None
        return segments_dir / f"{segment_cache_key(item, quality)}.mp4"

    def _render_item(item: dict[str, Any]) -> pathlib.Path:
        out_path: pathlib.Path = item["out_path"]
        cached = _cache_path(item)

        if cached is not None and cached.exists():
            shutil.copy2(cached, out_path)
            return out_path

        if item["type"] == "gap":
            seg_id = item["segment_id"]
            gap_dur = item["gap_duration"]
            print(
                f"[eve text-edit] Preserving {gap_dur:.2f}s gap "
                f"before segment {seg_id}",
                flush=True,
            )
            gap_cmd = build_trim_command(
                item["source_path"],
                item["gap_bounds"][1],
                item["gap_bounds"][2],
                out_path,
                quality,
                scale,
                codec,
            )
            run_ffmpeg(
                gap_cmd,
                context=f"preserving gap before segment {item['segment_id']}",
            )
            if cached is not None and out_path.exists():
                shutil.copy2(out_path, cached)
            return out_path

        description = item["description"]
        print(
            (
                f"[eve text-edit] Rendering "
                f"{item['display_index']}/{total_units}: {description}"
            ),
            flush=True,
        )

        source_path = item["source_path"]
        start_val = item["start_val"]
        end_val = item["end_val"]
        audio_policy = item["audio_policy"]
        overlay_duration = item["overlay_duration"]
        chain_meta = item["chain_meta"]

        if item["key"] and chain_meta:
            prepared_path = pathlib.Path(chain_meta["prepared_path"])
            prepared_pix_fmt = str(chain_meta.get("prepared_pix_fmt") or "yuv420p")
            base_offset = float(chain_meta.get("base_offset", 0.0))
            effective_offset = base_offset + float(
                item["segment_info"].get("offset", 0.0)
            )

            prepared_broll = dict(item["broll"])
            prepared_broll["file"] = str(prepared_path)
            prepared_broll["still"] = False
            prepared_broll.pop("placeholders", None)
            prepared_broll.pop("overlays", None)
            prepared_broll.pop("continue", None)
            prepared_broll.pop("duration", None)
            prepared_broll.pop("start_offset", None)

            if source_path is not None:
                cmd = build_broll_command(
                    source_path,
                    start_val,
                    end_val,
                    prepared_broll,
                    out_path,
                    working,
                    effective_offset=effective_offset,
                    effective_duration=overlay_duration,
                    quality=quality,
                    scale=scale,
                    codec=codec,
                )
                run_ffmpeg(cmd, context=f"rendering {description}")
            else:
                _svf = _make_scale_vf(scale)
                trim_cmd = [
                    FFMPEG,
                    "-y",
                    "-ss",
                    f"{effective_offset:.3f}",
                    "-t",
                    f"{overlay_duration:.3f}",
                    "-i",
                    str(prepared_path),
                ]
                if _svf:
                    trim_cmd.extend(["-vf", _svf])
                trim_cmd.extend(encoding_args(quality, codec))
                trim_cmd.extend(["-pix_fmt", prepared_pix_fmt])
                if audio_policy == "broll":
                    trim_cmd.extend(["-c:a", "aac"])
                else:
                    trim_cmd.extend(["-an"])
                trim_cmd.append(str(out_path))
                run_ffmpeg(trim_cmd, context=f"rendering {description}")
        else:
            if not source_path:
                raise ValueError(
                    f"Segment {item['segment'].get('id')} missing source and b-roll"
                )
            render_source_ranges(
                source_path,
                segment_media_ranges(item["segment"]),
                out_path,
                working,
                context=f"rendering {description}",
                quality=quality,
                scale=scale,
                codec=codec,
            )

        if cached is not None and out_path.exists():
            shutil.copy2(out_path, cached)

        return out_path

    mhash = _manifest_hash(manifest) if cache_dir else ""
    chash = _render_config_hash(quality, codec, scale) if cache_dir else ""
    checkpoint: dict[str, Any] = {}
    if cache_dir and resume:
        cp = _read_checkpoint(cache_dir)
        if cp.get("manifest_hash") == mhash and cp.get("render_config_hash") == chash:
            checkpoint = cp

    completed_set: set[int] = set(int(x) for x in checkpoint.get("completed", []))
    stored_files: dict[int, str] = {
        int(k): v for k, v in (checkpoint.get("segment_files") or {}).items()
    }

    cp_completed: list[int] = sorted(completed_set)
    cp_files: dict[int, str] = dict(stored_files)

    def _try_checkpoint(i: int, out_path: pathlib.Path) -> bool:
        if i not in completed_set:
            return False
        src = pathlib.Path(stored_files[i])
        if not src.exists():
            return False
        shutil.copy2(src, out_path)
        return True

    def _update_checkpoint(i: int, path: pathlib.Path) -> None:
        if cache_dir is None:
            return
        cached = _cache_path(work_items[i])
        stored = str(cached) if (cached and cached.exists()) else str(path)
        cp_completed.append(i)
        cp_files[i] = stored
        _write_checkpoint(cache_dir, mhash, chash, sorted(cp_completed), cp_files)

    render_times: list[float] = []

    def _render_item_timed(item: dict[str, Any], idx: int) -> pathlib.Path:
        out_path: pathlib.Path = item["out_path"]
        if _try_checkpoint(idx, out_path):
            return out_path
        cached_check = _cache_path(item)
        if cached_check is not None and cached_check.exists():
            result = _render_item(item)
            _update_checkpoint(idx, result)
            return result
        t0 = time.monotonic()
        result = _render_item(item)
        elapsed = time.monotonic() - t0
        if item["type"] == "segment":
            render_times.append(elapsed)
        _update_checkpoint(idx, result)
        return result

    max_workers = jobs if jobs > 0 else min(os.cpu_count() or 1, 8)

    if max_workers <= 1 or len(work_items) <= 1:
        outputs = [_render_item_timed(item, i) for i, item in enumerate(work_items)]
    else:
        index_by_future: dict[Any, int] = {}
        result_by_index: dict[int, pathlib.Path] = {}

        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            for i, item in enumerate(work_items):
                index_by_future[pool.submit(_render_item_timed, item, i)] = i
            for future in as_completed(index_by_future):
                result_by_index[index_by_future[future]] = future.result()

        outputs = [result_by_index[i] for i in range(len(work_items))]

    if cache_dir is not None and render_times:
        _update_timing(cache_dir, quality, sum(render_times), len(render_times))

    return outputs


def concat_segments(segments: list[pathlib.Path], destination: pathlib.Path) -> None:
    list_file = destination.parent / "concat_list.txt"
    with list_file.open("w", encoding="utf-8") as file_handle:
        for segment in segments:
            file_handle.write(f"file '{segment}'\n")

    print(
        f"[eve text-edit] Concatenating {len(segments)} rendered clips",
        flush=True,
    )
    cmd = [
        FFMPEG,
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(list_file),
        "-c",
        "copy",
        str(destination),
    ]
    run_ffmpeg(cmd, context="concatenating rendered segments")


def write_manifest(manifest: dict[str, Any], path: pathlib.Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    print(f"[eve text-edit] Writing manifest to {path}", flush=True)
    with path.open("w", encoding="utf-8") as file_handle:
        json.dump(manifest, file_handle, indent=2, ensure_ascii=False)


def collect_markers(
    manifest: dict[str, Any], preserve_gap_threshold: float | None = None
) -> list[tuple[str, float]]:
    timeline = 0.0
    markers: list[tuple[str, float]] = []
    previous_source_segment: dict[str, Any] | None = None

    for segment in manifest.get("segments", []):
        if preserve_gap_threshold is not None and previous_source_segment is not None:
            gap = compute_gap(previous_source_segment, segment)
            if gap is not None:
                _, gap_start, gap_end = gap
                gap_duration = gap_end - gap_start
                if gap_duration > 0 and gap_duration <= preserve_gap_threshold:
                    timeline += gap_duration

        kind = segment_kind(segment)
        if kind == "marker":
            if edit_deleted(segment):
                continue
            title = (
                str(segment.get("title") or "").strip()
                or str(segment.get("text") or "").strip()
                or str(segment.get("id") or "marker")
            )
            markers.append((title, timeline))
            continue

        duration = segment_duration(segment)
        if duration <= 0:
            continue

        previous_source_segment = segment
        if edit_deleted(segment):
            continue

        timeline += duration

    return markers


def _read_meta(cache_dir: pathlib.Path) -> dict[str, Any]:
    path = cache_dir / "meta.json"
    if path.exists():
        try:
            with path.open("r", encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, dict):
                return data
        except Exception:
            pass
    return {}


def _write_meta(cache_dir: pathlib.Path, meta: dict[str, Any]) -> None:
    path = cache_dir / "meta.json"
    try:
        with path.open("w", encoding="utf-8") as fh:
            json.dump(meta, fh, indent=2)
    except Exception:
        pass


def _update_timing(
    cache_dir: pathlib.Path, quality: str, elapsed: float, count: int
) -> None:
    if count == 0:
        return
    meta = _read_meta(cache_dir)
    timings: dict[str, Any] = meta.get("avg_seconds_per_segment", {})
    prev = float(timings.get(quality, 0.0))
    prev_count = int(meta.get("sample_count", {}).get(quality, 0))
    new_count = prev_count + count
    new_avg = (prev * prev_count + elapsed) / new_count
    timings[quality] = round(new_avg, 3)
    samples: dict[str, Any] = meta.get("sample_count", {})
    samples[quality] = new_count
    meta["avg_seconds_per_segment"] = timings
    meta["sample_count"] = samples
    _write_meta(cache_dir, meta)


def analyze_render(
    manifest: dict[str, Any],
    quality: str,
    scale: float,
    cache_dir: pathlib.Path | None,
    preserve_gap_threshold: float | None = None,
) -> dict[str, Any]:
    id_to_source = {
        item["id"]: pathlib.Path(str(item["file"])).expanduser()
        for item in manifest.get("sources", [])
    }
    manifest_segments = manifest.get("segments", [])
    chain_map, chains = compute_broll_chains(manifest_segments)
    segments_dir = (cache_dir / "segments") if cache_dir else None

    def to_float(value: Any, default: float = 0.0) -> float:
        try:
            return float(value)
        except Exception:
            return default

    total = 0
    cached_count = 0
    missed_ids: list[str] = []

    for segment in manifest_segments:
        kind = segment_kind(segment)
        key = canonical_broll_key(segment)
        has_broll = bool(key)
        is_marker = kind == "marker"

        if deleted_marker(segment):
            continue
        if is_marker and not has_broll:
            continue

        source_id = segment.get("source")
        source_path = id_to_source.get(source_id) if source_id in id_to_source else None
        duration = segment_duration(segment)

        if duration <= 0 and not has_broll:
            continue
        if not is_marker and edit_deleted(segment):
            continue

        segment_info = chain_map.get(
            id(segment),
            {
                "chain_id": None,
                "offset": 0.0,
                "overlay_duration": duration,
                "duration": duration,
            },
        )
        chain_id = segment_info.get("chain_id")
        chain_meta = chains.get(chain_id) if isinstance(chain_id, int) else None
        overlay_duration = segment_info.get("overlay_duration", duration)

        item: dict[str, Any] = {
            "type": "segment",
            "source_path": source_path,
            "segment": segment,
            "key": key,
            "chain_meta": chain_meta,
            "segment_info": segment_info,
            "overlay_duration": overlay_duration,
        }

        total += 1
        ck = segment_cache_key(item, quality)
        if segments_dir is not None and (segments_dir / f"{ck}.mp4").exists():
            cached_count += 1
        else:
            missed_ids.append(str(segment.get("id") or ""))

    missed = total - cached_count
    avg_secs = 0.0
    if cache_dir is not None:
        meta = _read_meta(cache_dir)
        avg_secs = float((meta.get("avg_seconds_per_segment") or {}).get(quality, 0.0))

    return {
        "total_segments": total,
        "cached_segments": cached_count,
        "changed_segments": missed,
        "changed_ids": missed_ids,
        "estimated_seconds": round(avg_secs * missed, 1) if avg_secs > 0 else None,
        "cache_hit_rate": round(cached_count / total, 3) if total else 1.0,
    }


def validate_manifest_for_render(
    manifest: dict[str, Any],
    base_dir: pathlib.Path,
) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []

    id_to_source: dict[str, pathlib.Path] = {}
    for item in manifest.get("sources", []):
        sid = item.get("id", "")
        fval = item.get("file", "")
        path = pathlib.Path(str(fval)).expanduser()
        id_to_source[sid] = path
        if not path.exists():
            errors.append(f"Source '{sid}': file not found: {path}")

    probed: dict[str, dict[str, Any]] = {}
    for sid, path in id_to_source.items():
        if path.exists():
            try:
                probed[sid] = probe_video_characteristics(path)
            except Exception:
                warnings.append(
                    f"Source '{sid}': could not probe video characteristics"
                )

    first_resolution: tuple[int, int] | None = None
    for sid, info in probed.items():
        w = int(info.get("width") or 0)
        h = int(info.get("height") or 0)
        if first_resolution is None:
            first_resolution = (w, h)
        elif (w, h) != first_resolution:
            warnings.append(
                f"Source '{sid}': resolution {w}×{h} differs from"
                f" {first_resolution[0]}×{first_resolution[1]}; will be rescaled"
            )

    for i, seg in enumerate(manifest.get("segments", [])):
        sid_val = seg.get("id") or f"segments[{i}]"
        kind = str(seg.get("kind") or "").lower()

        if kind == "marker":
            broll = (seg.get("edit") or {}).get("broll") or seg.get("broll")
            if broll and broll.get("file"):
                broll_path = pathlib.Path(str(broll["file"])).expanduser()
                if not broll_path.exists():
                    errors.append(
                        f"Segment '{sid_val}': b-roll file not found: {broll_path}"
                    )
            continue

        broll = normalized_segment_broll(seg)
        if broll and broll.get("file"):
            broll_path = pathlib.Path(str(broll["file"])).expanduser()
            if not broll_path.exists():
                errors.append(
                    f"Segment '{sid_val}': b-roll file not found: {broll_path}"
                )

        start_val = seg.get("start_tick") or seg.get("start")
        end_val = seg.get("end_tick") or seg.get("end")
        if start_val is not None and end_val is not None:
            try:
                s, e = float(start_val), float(end_val)
                if e <= s:
                    errors.append(
                        f"Segment '{sid_val}': non-positive duration"
                        f" (start={s}, end={e})"
                    )
            except (TypeError, ValueError):
                errors.append(f"Segment '{sid_val}': invalid start/end values")

        for j, word in enumerate(seg.get("words", [])):
            ws = word.get("start_tick") or word.get("start")
            we = word.get("end_tick") or word.get("end")
            if ws is not None and we is not None:
                try:
                    wstart, wend = float(ws), float(we)
                    if wend <= wstart:
                        errors.append(
                            f"Segment '{sid_val}' word[{j}]: non-positive duration"
                        )
                    if start_val is not None and end_val is not None:
                        seg_s, seg_e = float(start_val), float(end_val)
                        if wstart < seg_s or wend > seg_e:
                            warnings.append(
                                f"Segment '{sid_val}' word[{j}]: "
                                f"bounds ({wstart}, {wend}) fall outside segment"
                            )
                except (TypeError, ValueError):
                    pass

    return errors, warnings


def _manifest_hash(manifest: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(manifest, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()[:16]


def _render_config_hash(quality: str, codec: str, scale: float) -> str:
    return hashlib.sha256(
        json.dumps(
            {"quality": quality, "codec": codec, "scale": round(scale, 6)}
        ).encode()
    ).hexdigest()[:16]


def _read_checkpoint(cache_dir: pathlib.Path) -> dict[str, Any]:
    path = cache_dir / "checkpoint.json"
    if path.exists():
        try:
            with path.open("r", encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, dict):
                return data
        except Exception:
            pass
    return {}


def _write_checkpoint(
    cache_dir: pathlib.Path,
    manifest_hash: str,
    config_hash: str,
    completed: list[int],
    segment_files: dict[int, str],
) -> None:
    path = cache_dir / "checkpoint.json"
    try:
        with path.open("w", encoding="utf-8") as fh:
            json.dump(
                {
                    "manifest_hash": manifest_hash,
                    "render_config_hash": config_hash,
                    "completed": completed,
                    "segment_files": {str(k): v for k, v in segment_files.items()},
                },
                fh,
            )
    except Exception:
        pass


def _clear_checkpoint(cache_dir: pathlib.Path) -> None:
    path = cache_dir / "checkpoint.json"
    path.unlink(missing_ok=True)


def _evict_cache(segments_dir: pathlib.Path, max_bytes: int) -> None:
    entries = sorted(
        segments_dir.glob("*.mp4"),
        key=lambda p: p.stat().st_atime,
    )
    total = sum(p.stat().st_size for p in entries)
    for entry in entries:
        if total <= max_bytes:
            break
        total -= entry.stat().st_size
        entry.unlink(missing_ok=True)


def run(args: Namespace) -> int:
    manifest_path = pathlib.Path(args.manifest).expanduser()
    manifest = load_manifest(manifest_path)

    if getattr(args, "validate", False):
        errors, warnings_list = validate_manifest_for_render(
            manifest, manifest_path.parent
        )
        result = {
            "valid": len(errors) == 0,
            "errors": [{"message": e} for e in errors],
            "warnings": [{"message": w} for w in warnings_list],
        }
        if getattr(args, "json", False):
            import sys

            print(json.dumps(result, indent=2), file=sys.stdout)
        else:
            for w in warnings_list:
                print(f"[eve text-edit] WARNING: {w}", flush=True)
            for e in errors:
                print(f"[eve text-edit] ERROR: {e}", flush=True)
            if result["valid"]:
                print("[eve text-edit] Manifest valid.", flush=True)
        return 0 if result["valid"] else 1

    # ── Resolve effective profile ──────────────────────────────────────────
    # Quality determines the default codec and scale.  Explicit --codec or
    # --scale on the command line override the profile default.
    quality = args.quality
    raw_codec = getattr(args, "codec", None)
    raw_scale = getattr(args, "scale", None)

    if quality == QUALITY_DRAFT:
        effective_codec = raw_codec if raw_codec else CODEC_MJPEG
        effective_scale = raw_scale if raw_scale is not None else 1.0
    else:
        effective_codec = CODEC_H264
        effective_scale = 1.0

    use_cache = not getattr(args, "no_cache", False)
    cache_dir: pathlib.Path | None
    if use_cache:
        raw = getattr(args, "cache_dir", None)
        resolved: pathlib.Path = (
            pathlib.Path(raw).expanduser()
            if raw
            else manifest_path.parent / ".eve-cache"
        )
        resolved.mkdir(parents=True, exist_ok=True)
        cache_dir = resolved
    else:
        cache_dir = None

    if getattr(args, "dry_run", False):
        import sys

        analysis = analyze_render(
            manifest,
            quality=quality,
            scale=effective_scale,
            cache_dir=cache_dir,
            preserve_gap_threshold=args.preserve_short_gaps,
        )
        if getattr(args, "json", False):
            print(json.dumps(analysis, indent=2), file=sys.stdout)
        else:
            changed = analysis["changed_segments"]
            total = analysis["total_segments"]
            eta = analysis.get("estimated_seconds")
            eta_str = f" (~{eta:.0f}s)" if eta is not None else ""
            print(
                f"[eve text-edit] {changed}/{total} segments need re-encoding{eta_str}",
                flush=True,
            )
        return 0

    segment_filter: set[str] | None = None
    segment_ids_arg = getattr(args, "segments", None)
    if segment_ids_arg:
        segment_filter = set(segment_ids_arg)
        render_manifest = dict(manifest)
        render_manifest["segments"] = [
            seg
            for seg in manifest.get("segments", [])
            if seg.get("id") in segment_filter
        ]
    else:
        render_manifest = manifest

    markers = collect_markers(
        manifest,
        preserve_gap_threshold=args.preserve_short_gaps,
    )

    working_base = pathlib.Path(args.workdir).expanduser() if args.workdir else None

    with tempfile.TemporaryDirectory(dir=working_base) as temporary_directory:
        working = pathlib.Path(temporary_directory)
        segments = render_segments(
            render_manifest,
            manifest_path.parent,
            working,
            preserve_gap_threshold=args.preserve_short_gaps,
            quality=quality,
            jobs=args.jobs,
            cache_dir=cache_dir,
            scale=effective_scale,
            codec=effective_codec,
            resume=not getattr(args, "no_resume", False),
        )

        if segment_filter is not None:
            print("[eve text-edit] Segment pre-render complete.", flush=True)
            return 0

        output_path = pathlib.Path(args.output).expanduser()
        output_path.parent.mkdir(parents=True, exist_ok=True)

        partial_every = getattr(args, "partial_every", 0)
        partial_path = output_path.with_suffix(".partial.mp4")
        if partial_every > 0 and len(segments) >= partial_every:
            import sys

            for batch_end in range(partial_every, len(segments), partial_every):
                batch = segments[:batch_end]
                concat_segments(batch, partial_path)
                event = {
                    "event": "partial",
                    "segments": batch_end,
                    "total": len(segments),
                    "file": str(partial_path),
                }
                print(json.dumps(event), file=sys.stdout, flush=True)

        concat_segments(segments, output_path)
        if partial_path.exists():
            partial_path.unlink(missing_ok=True)

        if args.subtitles is not None:
            subtitles_path = (
                output_path.with_suffix(".vtt")
                if args.subtitles == ""
                else pathlib.Path(args.subtitles).expanduser()
            )
            cues = build_subtitle_cues(
                manifest,
                preserve_gap_threshold=args.preserve_short_gaps,
            )
            write_webvtt(cues, subtitles_path)
            if not args.no_subtitle_mux:
                print(
                    (
                        "[eve text-edit] Muxing subtitles from "
                        f"{subtitles_path} into {output_path}"
                    ),
                    flush=True,
                )
                mux_subtitles(output_path, subtitles_path)

        if args.pretty_manifest:
            out_manifest = pathlib.Path(args.pretty_manifest).expanduser()
            write_manifest(manifest, out_manifest)

        print(f"[eve text-edit] Final cut available at {output_path}", flush=True)
        for title, stamp in markers:
            print(f"[{format_minsec(stamp)}] {title}", flush=True)

        if cache_dir is not None:
            _clear_checkpoint(cache_dir)
            snapshot_path = cache_dir / "manifest.json"
            with snapshot_path.open("w", encoding="utf-8") as fh:
                json.dump(manifest, fh, indent=2, ensure_ascii=False)
            segments_dir = cache_dir / "segments"
            if segments_dir.is_dir():
                max_bytes = int(getattr(args, "cache_max_size", 10.0) * 1024**3)
                _evict_cache(segments_dir, max_bytes)

    return 0
