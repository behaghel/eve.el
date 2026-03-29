from __future__ import annotations

import argparse
import importlib
import json
import os
import pathlib
import subprocess
import sys
import tempfile
from argparse import ArgumentParser, Namespace, _SubParsersAction
from typing import Any

from .common import add_json_flag

FFMPEG = "ffmpeg"


def positive_float(value: str) -> float:
    try:
        parsed = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc
    if parsed < 0:
        raise argparse.ArgumentTypeError("must be >= 0")
    return parsed


def ensure_dir(path: pathlib.Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def ffmpeg_extract(input_path: pathlib.Path, output_path: pathlib.Path) -> None:
    cmd = [
        FFMPEG,
        "-y",
        "-i",
        str(input_path),
        "-ac",
        "1",
        "-ar",
        "16000",
        "-vn",
        str(output_path),
    ]
    subprocess.run(cmd, check=True, capture_output=True)


def split_segment(
    source_id: str,
    index: int,
    start: float,
    words: list[dict[str, Any]],
    max_duration: float,
) -> list[dict[str, Any]]:
    buckets: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    bucket_start = start

    for word in words:
        if not current:
            bucket_start = word["start"] or bucket_start
        bucket_end = word["end"] or bucket_start
        duration = (bucket_end or bucket_start) - bucket_start
        if duration > max_duration and current:
            buckets.append(current)
            current = [word]
            bucket_start = word["start"] or bucket_start
        else:
            current.append(word)
    if current:
        buckets.append(current)

    slices: list[dict[str, Any]] = []
    for bucket_index, bucket in enumerate(buckets, start=0):
        slice_start = bucket[0]["start"] or start
        slice_end = bucket[-1]["end"] or slice_start
        slices.append(
            {
                "id": f"{source_id}-s{index:04d}-{bucket_index}",
                "source": source_id,
                "start": round(slice_start, 3),
                "end": round(slice_end, 3),
                "speaker": None,
                "text": " ".join(token["token"] for token in bucket),
                "words": bucket,
                "tags": [],
                "notes": "",
                "broll": None,
            }
        )
    return slices


def segment_to_dict(
    source_id: str,
    index: int,
    segment: Any,
    max_duration: float,
) -> list[dict[str, Any]]:
    words = [
        {
            "start": round(word.start, 3) if word.start is not None else None,
            "end": round(word.end, 3) if word.end is not None else None,
            "token": word.word.strip(),
        }
        for word in segment.words or []
        if word.word.strip()
    ]

    text = " ".join(word["token"] for word in words) if words else segment.text.strip()
    start = round(segment.start or 0.0, 3)
    end = round(segment.end or start, 3)

    if max_duration and words:
        return split_segment(source_id, index, start, words, max_duration)

    return [
        {
            "id": f"{source_id}-s{index:04d}",
            "source": source_id,
            "start": start,
            "end": end,
            "speaker": getattr(segment, "speaker", None),
            "text": text,
            "words": words,
            "tags": [],
            "notes": "",
            "broll": None,
        }
    ]


def _load_whisper_model() -> Any:
    module = importlib.import_module("faster_whisper")
    return module.WhisperModel


def register(subparsers: _SubParsersAction[ArgumentParser]) -> None:
    parser = subparsers.add_parser(
        "transcribe",
        help="Generate a TJM manifest from media sources.",
    )
    parser.add_argument("inputs", nargs="+", help="Input media files (audio or video)")
    parser.add_argument("--output", required=True, help="Manifest JSON path to write")
    parser.add_argument(
        "--model",
        default="base.en",
        help="faster-whisper model name/path (default: base.en)",
    )
    parser.add_argument(
        "--language",
        default="en",
        help="Language hint passed to the model",
    )
    parser.add_argument(
        "--beam-size", type=int, default=5, help="Beam size for decoding"
    )
    parser.add_argument(
        "--device",
        default="auto",
        help="Device override for faster-whisper (auto / cpu / cuda)",
    )
    parser.add_argument(
        "--pretty", action="store_true", help="Pretty-print JSON (indentation)"
    )
    parser.add_argument(
        "--max-segment-duration",
        type=positive_float,
        default=0.0,
        help=(
            "Optional maximum segment duration in seconds; segments longer than this "
            "are split at word boundaries."
        ),
    )
    parser.add_argument("--stub", action="store_true", help=argparse.SUPPRESS)
    add_json_flag(parser)
    parser.set_defaults(handler=run, command="transcribe")


def run(args: Namespace) -> int:
    inputs = [pathlib.Path(path).expanduser() for path in args.inputs]
    for path in inputs:
        if not path.exists():
            print(f"eve transcribe: input '{path}' not found", file=sys.stderr)
            return 1

    stub_mode = args.stub or os.environ.get("VIDEO_TRANSCRIBE_STUB")
    model: Any = None
    if not stub_mode:
        whisper_model = _load_whisper_model()
        model = whisper_model(args.model, device=args.device, compute_type="int8")

    manifest_sources: list[dict[str, Any]] = []
    manifest_segments: list[dict[str, Any]] = []

    with tempfile.TemporaryDirectory() as temporary_directory:
        temp_dir = pathlib.Path(temporary_directory)
        for index, media_path in enumerate(inputs, start=1):
            source_id = f"clip{index:02d}"
            manifest_sources.append({"id": source_id, "file": str(media_path)})

            if stub_mode:
                continue

            wav_path = temp_dir / f"audio_{index:02d}.wav"
            ffmpeg_extract(media_path, wav_path)

            segments, _info = model.transcribe(
                str(wav_path),
                beam_size=args.beam_size,
                language=args.language,
                vad_filter=False,
                word_timestamps=True,
            )

            for segment_index, segment in enumerate(segments, start=1):
                manifest_segments.extend(
                    segment_to_dict(
                        source_id,
                        segment_index,
                        segment,
                        args.max_segment_duration,
                    )
                )

    manifest = {
        "version": 1,
        "sources": manifest_sources,
        "segments": manifest_segments,
    }

    output_path = pathlib.Path(args.output).expanduser()
    ensure_dir(output_path)
    with output_path.open("w", encoding="utf-8") as file_handle:
        if args.pretty:
            json.dump(manifest, file_handle, indent=2, ensure_ascii=False)
        else:
            json.dump(manifest, file_handle, separators=(",", ":"), ensure_ascii=False)
            file_handle.write("\n")

    return 0
