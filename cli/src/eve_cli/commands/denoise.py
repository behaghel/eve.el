from __future__ import annotations

import os
import subprocess
import sys
import urllib.request
from argparse import ArgumentParser, Namespace, _SubParsersAction
from pathlib import Path

from .common import add_json_flag

FFMPEG = "ffmpeg"
FFPROBE = "ffprobe"
DEFAULT_MODEL_URL = (
    "https://raw.githubusercontent.com/GregorR/rnnoise-models/master/"
    "somnolent-hogwash-2018-09-01/sh.rnnn"
)
DEFAULT_MODEL_NAME = "sh.rnnn"


def register(subparsers: _SubParsersAction[ArgumentParser]) -> None:
    parser = subparsers.add_parser(
        "denoise",
        help="Denoise the primary audio stream while preserving video output.",
    )
    parser.add_argument(
        "-m", "--model", default=None, help="Optional RNNoise model path"
    )
    parser.add_argument(
        "-f",
        "--extra-filter",
        default="",
        help="Additional ffmpeg audio filters to append after denoising",
    )
    parser.add_argument(
        "-C",
        "--copy-video",
        action="store_true",
        help="Copy the original video stream instead of re-encoding it",
    )
    parser.add_argument("input", help="Input media file.")
    parser.add_argument("output", nargs="?", help="Optional output media file.")
    add_json_flag(parser)
    parser.set_defaults(handler=run, command="denoise")


def default_output_path(input_path: Path) -> Path:
    if input_path.suffix:
        return input_path.with_name(f"{input_path.stem}.denoise{input_path.suffix}")
    return input_path.with_name(f"{input_path.name}.denoise")


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


def _resolve_model_path(cli_model: str | None) -> Path:
    if cli_model:
        return Path(cli_model).expanduser()

    env_model = os.environ.get("ARNNDN_MODEL")
    if env_model:
        return Path(env_model).expanduser()

    cache_dir = Path.home() / ".cache" / "eve-cli" / "rnnoise"
    cache_dir.mkdir(parents=True, exist_ok=True)
    model_path = cache_dir / DEFAULT_MODEL_NAME
    if not model_path.exists():
        urllib.request.urlretrieve(DEFAULT_MODEL_URL, model_path)
    return model_path


def run(args: Namespace) -> int:
    input_path = Path(args.input).expanduser()
    output_path = (
        Path(args.output).expanduser()
        if args.output
        else default_output_path(input_path)
    )

    if not input_path.is_file():
        print(f"eve denoise: input '{input_path}' not found", file=sys.stderr)
        return 1

    model_path = _resolve_model_path(args.model)
    if not model_path.is_file():
        print(f"eve denoise: RNNoise model '{model_path}' not found", file=sys.stderr)
        return 1

    output_path.parent.mkdir(parents=True, exist_ok=True)

    audio_filter = f"arnndn=m={model_path},aresample=async=1:first_pts=0"
    if args.extra_filter:
        audio_filter = f"{audio_filter},{args.extra_filter}"

    has_video = _has_video(input_path)

    if has_video:
        if args.copy_video:
            cmd = [
                FFMPEG,
                "-y",
                "-i",
                str(input_path),
                "-filter:a",
                audio_filter,
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
        else:
            cmd = [
                FFMPEG,
                "-y",
                "-i",
                str(input_path),
                "-filter:a",
                audio_filter,
                "-c:v",
                "libx264",
                "-preset",
                "medium",
                "-crf",
                "18",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                "-movflags",
                "+faststart",
                str(output_path),
            ]
    else:
        cmd = [
            FFMPEG,
            "-y",
            "-i",
            str(input_path),
            "-af",
            audio_filter,
            str(output_path),
        ]

    subprocess.run(cmd, check=True)
    return 0
