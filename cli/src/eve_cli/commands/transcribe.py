from __future__ import annotations

import argparse
from dataclasses import dataclass
import importlib
import json
import os
import pathlib
import subprocess
import sys
import tempfile
from argparse import ArgumentParser, Namespace, _SubParsersAction
from typing import Any, Callable, Never

from .common import add_json_flag
from .tag_fillers import tag_manifest_fillers
from ..utils.fillers import build_filler_set

FFMPEG = "ffmpeg"
TRANSCRIBE_BACKENDS = ("faster-whisper", "transformers", "nemo")
VERBATIM_INITIAL_PROMPT = (
    "um, uh, ah, hmm... I, I mean, like, you know, so, uh, we were, um, "
    "talking and then-- and then I..."
)
BackendRunner = Callable[[Namespace, list[pathlib.Path]], list[dict[str, Any]]]


@dataclass
class BackendWord:
    start: float | None
    end: float | None
    word: str


@dataclass
class BackendSegment:
    start: float | None
    end: float | None
    text: str
    words: list[BackendWord]
    speaker: str | None = None


class MissingBackendDependencyError(RuntimeError):
    pass


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


def _raise_missing_backend_dependency(
    backend: str,
    *,
    modules: tuple[str, ...],
    extra: str,
    exc: ModuleNotFoundError,
) -> Never:
    packages = " and ".join(f"`{module}`" for module in modules)
    raise MissingBackendDependencyError(
        f"backend '{backend}' requires {packages}. "
        f"Install them with `uv sync --project cli --extra {extra}` from the repo root "
        f"or `uv pip install .[{extra}]` from `cli/`."
    ) from exc


def _coerce_float(value: Any) -> float | None:
    if value is None:
        return None
    return float(value)


def _value_from_mapping_or_attr(item: Any, key: str, default: Any = None) -> Any:
    if isinstance(item, dict):
        return item.get(key, default)
    return getattr(item, key, default)


def _make_backend_segment(
    *,
    text: str,
    words: list[BackendWord],
    start: float | None = None,
    end: float | None = None,
    speaker: str | None = None,
) -> BackendSegment:
    if start is None:
        start = next((word.start for word in words if word.start is not None), 0.0)
    if end is None:
        end = next(
            (word.end for word in reversed(words) if word.end is not None), start
        )
    return BackendSegment(
        start=start,
        end=end,
        text=text.strip() or " ".join(word.word for word in words),
        words=words,
        speaker=speaker,
    )


def _resolve_transformers_device(device: str, torch_module: Any) -> int:
    if device == "cpu":
        return -1
    if device == "cuda":
        return 0
    cuda = getattr(torch_module, "cuda", None)
    if cuda is not None and cuda.is_available():
        return 0
    return -1


def _transformers_result_to_segments(result: dict[str, Any]) -> list[BackendSegment]:
    words: list[BackendWord] = []
    for chunk in result.get("chunks") or []:
        token = str(chunk.get("text", "")).strip()
        if not token:
            continue
        timestamp = chunk.get("timestamp")
        start: float | None = None
        end: float | None = None
        if isinstance(timestamp, tuple | list) and len(timestamp) == 2:
            start = _coerce_float(timestamp[0])
            end = _coerce_float(timestamp[1])
        words.append(BackendWord(start=start, end=end, word=token))
    return [_make_backend_segment(text=str(result.get("text", "")), words=words)]


def _build_transformers_generate_kwargs(
    asr_pipeline: Any,
    args: Namespace,
) -> dict[str, Any]:
    generate_kwargs: dict[str, Any] = {}
    if args.language:
        generate_kwargs["language"] = args.language
    if args.verbatim:
        tokenizer = getattr(asr_pipeline, "tokenizer", None)
        get_prompt_ids = getattr(tokenizer, "get_prompt_ids", None)
        if callable(get_prompt_ids):
            generate_kwargs["prompt_ids"] = get_prompt_ids(VERBATIM_INITIAL_PROMPT)
        else:
            generate_kwargs["prompt"] = VERBATIM_INITIAL_PROMPT
    return generate_kwargs


def _nemo_words_from_timestep(timestep: Any) -> list[BackendWord]:
    if not isinstance(timestep, dict):
        return []
    word_entries = timestep.get("word") or timestep.get("words") or []
    words: list[BackendWord] = []
    for entry in word_entries:
        token = _value_from_mapping_or_attr(entry, "word")
        if token is None:
            token = _value_from_mapping_or_attr(entry, "text", "")
        token = str(token).strip()
        if not token:
            continue
        words.append(
            BackendWord(
                start=_coerce_float(
                    _value_from_mapping_or_attr(entry, "start", None)
                    if _value_from_mapping_or_attr(entry, "start", None) is not None
                    else _value_from_mapping_or_attr(entry, "start_offset", None)
                ),
                end=_coerce_float(
                    _value_from_mapping_or_attr(entry, "end", None)
                    if _value_from_mapping_or_attr(entry, "end", None) is not None
                    else _value_from_mapping_or_attr(entry, "end_offset", None)
                ),
                word=token,
            )
        )
    return words


def _nemo_result_to_segments(result: Any) -> list[BackendSegment]:
    text = str(_value_from_mapping_or_attr(result, "text", "")).strip()
    timestep = _value_from_mapping_or_attr(result, "timestep", None)
    if timestep is None:
        timestep = _value_from_mapping_or_attr(result, "timestamps", {})
    words = _nemo_words_from_timestep(timestep)
    if not isinstance(timestep, dict):
        return [_make_backend_segment(text=text, words=words)]

    segment_entries = timestep.get("segment") or timestep.get("segments") or []
    segments: list[BackendSegment] = []
    for entry in segment_entries:
        segment_start = _coerce_float(_value_from_mapping_or_attr(entry, "start", None))
        if segment_start is None:
            segment_start = _coerce_float(
                _value_from_mapping_or_attr(entry, "start_offset", None)
            )
        segment_end = _coerce_float(_value_from_mapping_or_attr(entry, "end", None))
        if segment_end is None:
            segment_end = _coerce_float(
                _value_from_mapping_or_attr(entry, "end_offset", None)
            )
        segment_text = str(
            _value_from_mapping_or_attr(entry, "segment", None)
            or _value_from_mapping_or_attr(entry, "text", "")
        ).strip()
        segment_words = [
            word
            for word in words
            if (
                segment_start is None
                or word.start is None
                or word.start >= segment_start
            )
            and (segment_end is None or word.end is None or word.end <= segment_end)
        ]
        segments.append(
            _make_backend_segment(
                text=segment_text,
                words=segment_words,
                start=segment_start,
                end=segment_end,
            )
        )

    if segments:
        return segments
    return [_make_backend_segment(text=text, words=words)]


def transcribe_faster_whisper(
    args: Namespace,
    inputs: list[pathlib.Path],
) -> list[dict[str, Any]]:
    whisper_model = _load_whisper_model()
    model = whisper_model(args.model, device=args.device, compute_type="int8")
    manifest_segments: list[dict[str, Any]] = []

    with tempfile.TemporaryDirectory() as temporary_directory:
        temp_dir = pathlib.Path(temporary_directory)
        for index, media_path in enumerate(inputs, start=1):
            source_id = f"clip{index:02d}"
            wav_path = temp_dir / f"audio_{index:02d}.wav"
            ffmpeg_extract(media_path, wav_path)

            transcribe_kwargs: dict[str, Any] = {
                "beam_size": args.beam_size,
                "language": args.language,
                "vad_filter": False,
                "word_timestamps": True,
            }
            if args.verbatim:
                transcribe_kwargs.update(
                    initial_prompt=VERBATIM_INITIAL_PROMPT,
                    condition_on_previous_text=False,
                    temperature=0.0,
                )

            segments, _info = model.transcribe(str(wav_path), **transcribe_kwargs)

            for segment_index, segment in enumerate(segments, start=1):
                manifest_segments.extend(
                    segment_to_dict(
                        source_id,
                        segment_index,
                        segment,
                        args.max_segment_duration,
                    )
                )

    return manifest_segments


def transcribe_transformers(
    args: Namespace,
    inputs: list[pathlib.Path],
) -> list[dict[str, Any]]:
    try:
        torch_module = importlib.import_module("torch")
        transformers_module = importlib.import_module("transformers")
    except ModuleNotFoundError as exc:
        _raise_missing_backend_dependency(
            "transformers",
            modules=("transformers", "torch"),
            extra="transformers",
            exc=exc,
        )

    asr_pipeline = transformers_module.pipeline(
        "automatic-speech-recognition",
        model=args.model,
        device=_resolve_transformers_device(args.device, torch_module),
        chunk_length_s=30,
    )
    manifest_segments: list[dict[str, Any]] = []

    with tempfile.TemporaryDirectory() as temporary_directory:
        temp_dir = pathlib.Path(temporary_directory)
        for index, media_path in enumerate(inputs, start=1):
            source_id = f"clip{index:02d}"
            wav_path = temp_dir / f"audio_{index:02d}.wav"
            ffmpeg_extract(media_path, wav_path)

            inference_kwargs: dict[str, Any] = {"return_timestamps": "word"}
            generate_kwargs = _build_transformers_generate_kwargs(asr_pipeline, args)
            if generate_kwargs:
                inference_kwargs["generate_kwargs"] = generate_kwargs

            result = asr_pipeline(str(wav_path), **inference_kwargs)
            for segment_index, segment in enumerate(
                _transformers_result_to_segments(result),
                start=1,
            ):
                manifest_segments.extend(
                    segment_to_dict(
                        source_id,
                        segment_index,
                        segment,
                        args.max_segment_duration,
                    )
                )

    return manifest_segments


def transcribe_nemo(
    args: Namespace,
    inputs: list[pathlib.Path],
) -> list[dict[str, Any]]:
    try:
        nemo_asr = importlib.import_module("nemo.collections.asr")
    except ModuleNotFoundError as exc:
        _raise_missing_backend_dependency(
            "nemo",
            modules=("nemo_toolkit", "torch"),
            extra="nemo",
            exc=exc,
        )

    model = nemo_asr.models.ASRModel.from_pretrained(model_name=args.model)
    if args.device in {"cpu", "cuda"} and hasattr(model, "to"):
        model = model.to(args.device)

    manifest_segments: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory() as temporary_directory:
        temp_dir = pathlib.Path(temporary_directory)
        for index, media_path in enumerate(inputs, start=1):
            source_id = f"clip{index:02d}"
            wav_path = temp_dir / f"audio_{index:02d}.wav"
            ffmpeg_extract(media_path, wav_path)

            results = model.transcribe([str(wav_path)], timestamps=True)
            for hypothesis in results:
                for segment_index, segment in enumerate(
                    _nemo_result_to_segments(hypothesis),
                    start=1,
                ):
                    manifest_segments.extend(
                        segment_to_dict(
                            source_id,
                            segment_index,
                            segment,
                            args.max_segment_duration,
                        )
                    )

    return manifest_segments


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
        "--backend",
        choices=TRANSCRIBE_BACKENDS,
        default="faster-whisper",
        help="Transcription backend to use (default: faster-whisper)",
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
    parser.add_argument(
        "--tag-fillers",
        action="store_true",
        help="Tag filler words in the output manifest before writing",
    )
    parser.add_argument(
        "--verbatim",
        action="store_true",
        help="Bias decoding toward verbatim disfluencies on supported backends",
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
    manifest_sources = [
        {"id": f"clip{index:02d}", "file": str(media_path)}
        for index, media_path in enumerate(inputs, start=1)
    ]
    manifest_segments: list[dict[str, Any]] = []

    backend_runners: dict[str, BackendRunner] = {
        "faster-whisper": transcribe_faster_whisper,
        "transformers": transcribe_transformers,
        "nemo": transcribe_nemo,
    }
    if not stub_mode:
        try:
            manifest_segments = backend_runners[args.backend](args, inputs)
        except MissingBackendDependencyError as exc:
            print(f"eve transcribe: {exc}", file=sys.stderr)
            return 2

    manifest = {
        "version": 1,
        "sources": manifest_sources,
        "segments": manifest_segments,
    }
    if args.tag_fillers:
        tag_manifest_fillers(manifest, build_filler_set())

    output_path = pathlib.Path(args.output).expanduser()
    ensure_dir(output_path)
    with output_path.open("w", encoding="utf-8") as file_handle:
        if args.pretty:
            json.dump(manifest, file_handle, indent=2, ensure_ascii=False)
        else:
            json.dump(manifest, file_handle, separators=(",", ":"), ensure_ascii=False)
            file_handle.write("\n")

    return 0
