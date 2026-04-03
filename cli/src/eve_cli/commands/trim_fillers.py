from __future__ import annotations

import importlib
import json
import os
import subprocess
import sys
import tempfile
from argparse import ArgumentParser, Namespace, _SubParsersAction
from pathlib import Path
from typing import Any

from .common import add_json_flag
from ..utils.fillers import DEFAULT_FILLERS, build_filler_set, is_filler

FFMPEG = "ffmpeg"
FFPROBE = "ffprobe"

DEFAULT_MODEL = "base.en"
DEFAULT_LANGUAGE = "en"
DEFAULT_PAD = 0.05


def _load_whisper_model() -> Any:
    module = importlib.import_module("faster_whisper")
    return module.WhisperModel


def _has_video(path: Path) -> bool:
    proc = subprocess.run(
        [
            FFPROBE,
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=index",
            "-of",
            "csv=p=0",
            str(path),
        ],
        capture_output=True,
        check=False,
    )
    return bool(proc.stdout.strip())


def _merge_ranges(ranges: list[tuple[float, float]]) -> list[tuple[float, float]]:
    if not ranges:
        return []
    merged: list[tuple[float, float]] = []
    current_start, current_end = ranges[0]
    for start, end in ranges[1:]:
        if start <= current_end:
            current_end = max(current_end, end)
        else:
            merged.append((current_start, current_end))
            current_start, current_end = start, end
    merged.append((current_start, current_end))
    return merged


def register(subparsers: _SubParsersAction[ArgumentParser]) -> None:
    parser = subparsers.add_parser(
        "trim-fillers",
        help="Trim filler words from an audio or video source.",
    )
    parser.add_argument("input", help="Input audio/video file")
    parser.add_argument("output", help="Output file path")
    parser.add_argument(
        "--model",
        default=os.environ.get("VIDEO_FILLER_MODEL", DEFAULT_MODEL),
        help="faster-whisper model name or path",
    )
    parser.add_argument(
        "--language",
        default=os.environ.get("VIDEO_FILLER_LANG", DEFAULT_LANGUAGE),
        help="Language hint for transcription",
    )
    parser.add_argument(
        "--pad",
        type=float,
        default=DEFAULT_PAD,
        help="Seconds to pad before/after each filler word",
    )
    parser.add_argument(
        "--filler",
        action="append",
        default=None,
        help="Additional filler word to remove (repeatable)",
    )
    parser.add_argument(
        "--video-codec",
        default="libx264",
        help="Video codec to use when re-encoding",
    )
    parser.add_argument(
        "--audio-codec",
        default="aac",
        help="Audio codec to use when re-encoding",
    )
    parser.add_argument(
        "--list-fillers",
        action="store_true",
        help="Print the filler list and exit",
    )
    parser.add_argument(
        "--save-ranges",
        default=None,
        help="Write JSON information about removed ranges",
    )
    add_json_flag(parser)
    parser.set_defaults(handler=run, command="trim-fillers")


def run(args: Namespace) -> int:
    print(
        "eve trim-fillers is deprecated and will be removed in a future release; use eve tag-fillers instead",
        file=sys.stderr,
    )

    if args.list_fillers:
        fillers = sorted({word.lower() for word in DEFAULT_FILLERS})
        if args.filler:
            fillers.extend([word.lower() for word in args.filler])
        for filler in sorted(set(fillers)):
            print(filler)
        return 0

    input_path = Path(args.input).expanduser()
    output_path = Path(args.output).expanduser()
    save_ranges_path = Path(args.save_ranges).expanduser() if args.save_ranges else None

    if not input_path.exists():
        print(
            f"eve trim-fillers: input file '{input_path}' does not exist",
            file=sys.stderr,
        )
        return 2

    fillers = [*DEFAULT_FILLERS, *(args.filler or [])]
    filler_set = build_filler_set(fillers)

    with tempfile.TemporaryDirectory() as temporary_directory:
        audio_path = Path(temporary_directory) / "audio.wav"
        extract_cmd = [
            FFMPEG,
            "-y",
            "-i",
            str(input_path),
            "-ac",
            "1",
            "-ar",
            "16000",
            "-vn",
            str(audio_path),
        ]
        subprocess.run(extract_cmd, check=True)

        whisper_model = _load_whisper_model()
        model = whisper_model(args.model, device="auto", compute_type="int8")
        segments, _info = model.transcribe(
            str(audio_path),
            beam_size=5,
            language=args.language,
            word_timestamps=True,
        )

        filler_ranges: list[tuple[float, float]] = []
        for segment in segments:
            if not segment.words:
                continue
            for word in segment.words:
                if word.start is None or word.end is None:
                    continue
                if is_filler(word.word, filler_set):
                    start = max(0.0, word.start - args.pad)
                    end = word.end + args.pad
                    if end > start:
                        filler_ranges.append((start, end))

    filler_ranges.sort()
    filler_ranges = _merge_ranges(filler_ranges)

    if save_ranges_path:
        ranges_doc = {
            "input": str(input_path),
            "output": str(output_path),
            "pad": args.pad,
            "filler_words": sorted(filler_set),
            "removed": [
                {"start": round(start, 4), "end": round(end, 4)}
                for start, end in filler_ranges
            ],
        }
        save_ranges_path.write_text(json.dumps(ranges_doc, indent=2), encoding="utf-8")

    if not filler_ranges:
        subprocess.run(
            [FFMPEG, "-y", "-i", str(input_path), "-c", "copy", str(output_path)],
            check=True,
        )
        return 0

    drop_tests = [f"between(t,{start:.3f},{end:.3f})" for start, end in filler_ranges]
    drop_expr = "+".join(drop_tests)
    select_expr = f"not({drop_expr})" if drop_expr else "1"

    has_video = _has_video(input_path)

    if has_video:
        filter_complex = (
            f"[0:v]select='{select_expr}',setpts=N/FRAME_RATE/TB[v];"
            f"[0:a]aselect='{select_expr}',asetpts=N/SR/TB[a]"
        )
        cmd = [
            FFMPEG,
            "-y",
            "-i",
            str(input_path),
            "-filter_complex",
            filter_complex,
            "-map",
            "[v]",
            "-map",
            "[a]",
            "-c:v",
            args.video_codec,
            "-preset",
            "medium",
            "-crf",
            "18",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            args.audio_codec,
            "-movflags",
            "+faststart",
            str(output_path),
        ]
    else:
        filter_complex = f"[0:a]aselect='{select_expr}',asetpts=N/SR/TB[a]"
        cmd = [
            FFMPEG,
            "-y",
            "-i",
            str(input_path),
            "-filter_complex",
            filter_complex,
            "-map",
            "[a]",
            "-c:a",
            args.audio_codec,
            str(output_path),
        ]

    subprocess.run(cmd, check=True)
    return 0
