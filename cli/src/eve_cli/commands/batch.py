from __future__ import annotations

import sys
from argparse import ArgumentParser, Namespace, _SubParsersAction
from pathlib import Path

from . import denoise, transcribe, trim_fillers
from .common import add_json_flag


def register(subparsers: _SubParsersAction[ArgumentParser]) -> None:
    parser = subparsers.add_parser(
        "batch",
        help="Run the end-to-end media processing pipeline over multiple inputs.",
    )
    parser.add_argument("--skip-denoise", action="store_true")
    parser.add_argument("--skip-trim", action="store_true")
    parser.add_argument("--skip-transcribe", action="store_true")
    parser.add_argument("--transcribe-manifest", default=None)
    parser.add_argument("--transcribe-model", default="")
    parser.add_argument("--transcribe-language", default="")
    parser.add_argument("--denoise-dir", default="")
    parser.add_argument("--trim-dir", default="")
    parser.add_argument("inputs", nargs="+", help="Input media files.")
    add_json_flag(parser)
    parser.set_defaults(handler=run, command="batch")


def default_transcribe_manifest(inputs: list[Path]) -> Path:
    first = inputs[0].name
    path = Path(first)
    if path.suffix:
        return Path(f"{path.stem}.tjm.json")
    return Path(f"{path.name}.tjm.json")


def denoise_out_path(input_path: Path, denoise_dir: str) -> Path:
    output = input_path.with_name(f"{input_path.stem}.denoise{input_path.suffix}")
    if denoise_dir:
        target_dir = Path(denoise_dir)
        target_dir.mkdir(parents=True, exist_ok=True)
        return target_dir / output.name
    return output


def trim_out_path(input_path: Path, trim_dir: str) -> Path:
    output = input_path.with_name(f"{input_path.stem}.trim{input_path.suffix}")
    if trim_dir:
        target_dir = Path(trim_dir)
        target_dir.mkdir(parents=True, exist_ok=True)
        return target_dir / output.name
    return output


def _transcribe_args(
    args: Namespace, inputs: list[Path], manifest_path: Path
) -> Namespace:
    return Namespace(
        inputs=[str(path) for path in inputs],
        output=str(manifest_path),
        model=args.transcribe_model or "base.en",
        language=args.transcribe_language or "en",
        beam_size=5,
        device="auto",
        pretty=True,
        max_segment_duration=0.0,
        stub=False,
        json=False,
        command="transcribe",
    )


def _denoise_args(input_path: Path, output_path: Path) -> Namespace:
    return Namespace(
        model=None,
        extra_filter="",
        copy_video=False,
        input=str(input_path),
        output=str(output_path),
        json=False,
        command="denoise",
    )


def _trim_args(input_path: Path, output_path: Path) -> Namespace:
    return Namespace(
        input=str(input_path),
        output=str(output_path),
        model="base.en",
        language="en",
        pad=0.05,
        filler=None,
        video_codec="libx264",
        audio_codec="aac",
        list_fillers=False,
        save_ranges=None,
        json=False,
        command="trim-fillers",
    )


def run(args: Namespace) -> int:
    input_paths = [Path(item).expanduser() for item in args.inputs]

    if args.skip_transcribe and args.skip_denoise and args.skip_trim:
        print("eve batch: all stages disabled", file=sys.stderr)
        return 1

    if not args.skip_transcribe:
        manifest_path = (
            Path(args.transcribe_manifest).expanduser()
            if args.transcribe_manifest
            else default_transcribe_manifest(input_paths)
        )
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        print(f"[eve batch] transcribe -> {manifest_path}")
        transcribe.run(_transcribe_args(args, input_paths, manifest_path))

    for input_path in input_paths:
        if not input_path.is_file():
            print(f"eve batch: input '{input_path}' not found", file=sys.stderr)
            continue

        current_input = input_path

        if not args.skip_denoise:
            denoised_path = denoise_out_path(current_input, args.denoise_dir)
            denoised_path.parent.mkdir(parents=True, exist_ok=True)
            print(f"[eve batch] denoise: {current_input} -> {denoised_path}")
            denoise.run(_denoise_args(current_input, denoised_path))
            current_input = denoised_path

        if not args.skip_trim:
            trimmed_path = trim_out_path(current_input, args.trim_dir)
            trimmed_path.parent.mkdir(parents=True, exist_ok=True)
            print(f"[eve batch] trim    : {current_input} -> {trimmed_path}")
            trim_fillers.run(_trim_args(current_input, trimmed_path))

    return 0
