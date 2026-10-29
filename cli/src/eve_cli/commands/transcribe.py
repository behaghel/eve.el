from __future__ import annotations

import argparse
import importlib
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import time
import wave
from argparse import ArgumentParser, Namespace, _SubParsersAction
from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass
from fractions import Fraction
from importlib.metadata import version as package_version
from typing import Any, Never

from .. import alignment, tjm_v11
from ..utils.fillers import DEFAULT_FILLERS
from .common import add_json_flag, write_json_atomic
from .tag_fillers import tag_manifest_fillers

FFMPEG = "ffmpeg"
FFPROBE = "ffprobe"
TRANSCRIBE_BACKENDS = ("faster-whisper", "transformers", "nemo")
VERBATIM_INITIAL_PROMPT = (
    "um, uh, ah, hmm... I, I mean, like, you know, so, uh, we were, um, "
    "talking and then-- and then I..."
)
DEFAULT_FILLER_HOTWORDS = "um uh ah hmm like you know I mean"
BackendRunner = Callable[[Namespace, list[pathlib.Path]], list[dict[str, Any]]]


def _emit_progress(args: Namespace, phase: str, **fields: Any) -> None:
    """Emit a JSON progress event when requested."""
    if not getattr(args, "progress_json", False):
        return
    payload = {"event": "progress", "phase": phase, **fields, "time": time.time()}
    print(json.dumps(payload, separators=(",", ":")), file=sys.stderr, flush=True)


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
        "-af",
        "aresample=async=1:first_pts=0",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-vn",
        str(output_path),
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True)
    except FileNotFoundError as exc:
        raise MissingBackendDependencyError(
            "ffmpeg executable not found; install ffmpeg or run from the devenv shell"
        ) from exc


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
            bucket_start = word["start"] if word["start"] is not None else bucket_start
        bucket_end = word["end"] if word["end"] is not None else bucket_start
        duration = (bucket_end or bucket_start) - bucket_start
        if duration > max_duration and current:
            buckets.append(current)
            current = [word]
            bucket_start = word["start"] if word["start"] is not None else bucket_start
        else:
            current.append(word)
    if current:
        buckets.append(current)

    slices: list[dict[str, Any]] = []
    for bucket_index, bucket in enumerate(buckets, start=0):
        slice_start = bucket[0]["start"] if bucket[0]["start"] is not None else start
        slice_end = bucket[-1]["end"] if bucket[-1]["end"] is not None else slice_start
        slices.append(
            {
                "id": f"{source_id}-s{index:04d}-{bucket_index}",
                "source": source_id,
                "start": slice_start,
                "end": slice_end,
                "speaker": None,
                "text": " ".join(token["token"] for token in bucket),
                "words": bucket,
                "tags": [],
                "notes": "",
                "broll": None,
            }
        )
    return slices


SENTENCE_ENDINGS = (".", "?", "!")
CLAUSE_BOUNDARIES = (",", ";", ":")
MIN_SENTENCE_WORDS = 2
CLAUSE_SEARCH_WINDOW = 5


def _find_clause_boundary(words: list[dict[str, Any]]) -> int | None:
    search_start = max(len(words) - CLAUSE_SEARCH_WINDOW, MIN_SENTENCE_WORDS - 1)
    for i in range(len(words) - 1, search_start - 1, -1):
        token = words[i]["token"].rstrip()
        if token.endswith(CLAUSE_BOUNDARIES):
            return i + 1
    return None


def _split_at_natural_boundaries(
    words: list[dict[str, Any]],
    max_words: int,
) -> list[list[dict[str, Any]]]:
    groups: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []

    for word in words:
        current.append(word)
        token = word["token"].rstrip()

        if token.endswith(SENTENCE_ENDINGS):
            if len(current) >= MIN_SENTENCE_WORDS:
                groups.append(current)
                current = []
        elif len(current) >= max_words:
            split_idx = _find_clause_boundary(current)
            if split_idx is not None and split_idx >= MIN_SENTENCE_WORDS:
                groups.append(current[:split_idx])
                current = current[split_idx:]
            else:
                groups.append(current)
                current = []

    if current:
        if groups and len(current) < MIN_SENTENCE_WORDS:
            groups[-1].extend(current)
        else:
            groups.append(current)

    return groups


def resegment_naturally(
    segments: list[dict[str, Any]],
    max_words: int,
) -> list[dict[str, Any]]:
    by_source: dict[str, list[dict[str, Any]]] = {}
    for seg in segments:
        by_source.setdefault(seg["source"], []).append(seg)

    result: list[dict[str, Any]] = []
    for source_id, source_segments in by_source.items():
        all_words: list[dict[str, Any]] = []
        for seg in source_segments:
            all_words.extend(seg.get("words", []))

        word_groups = _split_at_natural_boundaries(all_words, max_words)

        for idx, words in enumerate(word_groups, start=1):
            if not words:
                continue
            text = " ".join(w["token"] for w in words)
            start = words[0].get("start") or 0.0
            end = words[-1].get("end") or start
            result.append(
                {
                    "id": f"{source_id}-s{idx:04d}",
                    "source": source_id,
                    "start": start,
                    "end": end,
                    "speaker": None,
                    "text": text,
                    "words": words,
                    "tags": [],
                    "notes": "",
                    "broll": None,
                }
            )

    return result


def segment_to_dict(
    source_id: str,
    index: int,
    segment: Any,
    max_duration: float,
) -> list[dict[str, Any]]:
    words = [
        {
            "start": word.start,
            "end": word.end,
            "token": word.word.strip(),
        }
        for word in segment.words or []
        if word.word.strip()
    ]

    text = " ".join(word["token"] for word in words) if words else segment.text.strip()
    start = (
        segment.start
        if segment.start is not None
        else (words[0]["start"] if words else None)
    )
    end = (
        segment.end
        if segment.end is not None
        else (words[-1]["end"] if words else None)
    )

    if max_duration and words:
        if start is None:
            raise tjm_v11.ValidationError(
                f"Segment '{source_id}-s{index:04d}' lacks a timed start; "
                "audio-backed alignment repair is required"
            )
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


def probe_audio_sample_rate(path: pathlib.Path) -> int:
    """Use the source audio sample grid as its authoritative TJM clock."""
    try:
        result = subprocess.run(
            [
                FFPROBE,
                "-v",
                "error",
                "-select_streams",
                "a:0",
                "-show_entries",
                "stream=sample_rate",
                "-of",
                "json",
                str(path),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        rate = int(json.loads(result.stdout)["streams"][0]["sample_rate"])
        if rate <= 0:
            raise ValueError("non-positive sample rate")
        return rate
    except (
        OSError,
        subprocess.CalledProcessError,
        ValueError,
        TypeError,
        KeyError,
        IndexError,
    ) as error:
        raise tjm_v11.ValidationError(
            f"Cannot determine source audio sample rate for '{path}': {error}"
        ) from error


def _transcription_interval(
    item: dict[str, Any], rate: int, label: str
) -> tuple[int, int]:
    """Quantize once to nearest source samples, never stretch an ASR interval."""
    try:
        start = Fraction(str(item.get("start")))
        end = Fraction(str(item.get("end")))
    except (ValueError, ZeroDivisionError) as error:
        raise tjm_v11.ValidationError(
            f"{label}: missing or non-finite word timing "
            f"(start={item.get('start')}, end={item.get('end')})"
        ) from error
    start_tick, end_tick = round(start * rate), round(end * rate)
    if start < 0 or end <= start or end_tick <= start_tick:
        raise tjm_v11.ValidationError(
            f"{label}: unresolved timing (start={item.get('start')}, "
            f"end={item.get('end')}, ticks={start_tick}..{end_tick} at {rate} Hz); "
            "audio-backed alignment repair is required; no duration was invented"
        )
    return start_tick, end_tick


def _v11_segments(
    segments: list[dict[str, Any]], sample_rates: dict[str, int]
) -> list[dict[str, Any]]:
    canonical: list[dict[str, Any]] = []
    for segment in segments:
        label = f"Segment '{segment['id']}'"
        rate = sample_rates[segment["source"]]
        words: list[dict[str, Any]] = []
        if not segment.get("words"):
            raise tjm_v11.ValidationError(f"{label}: ASR returned no timed words")
        for raw in segment["words"]:
            spoken = str(raw.get("spoken") or raw.get("token") or "")
            word_start, word_end = _transcription_interval(
                raw, rate, f"{label}, token {spoken!r}"
            )
            word: dict[str, Any] = {
                "spoken": spoken,
                "start_tick": word_start,
                "end_tick": word_end,
                "start": word_start / rate,
                "end": word_end / rate,
            }
            if "display" in raw:
                word["display"] = raw["display"]
            edit = deepcopy(raw.get("edit") or {})
            if edit.get("kind") == "filler":
                edit.pop("kind")
                edit.setdefault("filler", True)
            if edit:
                word["edit"] = edit
            words.append(word)
        start_tick, end_tick = _transcription_interval(segment, rate, label)
        entry: dict[str, Any] = {
            "id": segment["id"],
            "source": segment["source"],
            "start_tick": start_tick,
            "end_tick": end_tick,
            "start": start_tick / rate,
            "end": end_tick / rate,
            "spoken_text": " ".join(word["spoken"] for word in words),
            "display_text": " ".join(
                word.get("display") or word["spoken"] for word in words
            ),
            "words": words,
        }
        if segment.get("speaker") is not None:
            entry["speaker"] = segment["speaker"]
        edit = deepcopy(segment.get("edit") or {})
        for key in ("tags", "notes", "broll"):
            if segment.get(key) is not None:
                edit.setdefault(key, deepcopy(segment[key]))
        if edit:
            entry["edit"] = edit
        canonical.append(entry)
    return canonical


def _partition_caption_envelopes(segments: list[dict[str, Any]]) -> None:
    """Partition intersecting source envelopes without touching any word data.

    Components are formed in source-time order, not with a greedy neighbor fix:
    a later, wide envelope can bridge two previously disjoint ranges. Only
    overlapping components change; touching captions and real gaps stay intact.
    """
    components: list[tuple[Fraction, Fraction, list[int]]] = []
    for left, right, index in sorted(
        (segment["start"], segment["end"], index)
        for index, segment in enumerate(segments)
    ):
        if components and left < components[-1][1]:
            start, end, members = components[-1]
            members.append(index)
            components[-1] = (start, max(end, right), members)
        else:
            components.append((left, right, [index]))
    for left, right, members in components:
        if len(members) == 1:
            continue
        members.sort()
        if members != list(range(members[0], members[-1] + 1)):
            raise tjm_v11.ValidationError(
                "Caption envelopes are not in source word order"
            )
        boundaries = [left]
        for previous, following in zip(members, members[1:]):
            word_end = segments[previous]["words"][-1]["end"]
            word_start = segments[following]["words"][0]["start"]
            if word_end > word_start:
                raise tjm_v11.ValidationError(
                    "Overlapping words cannot be fixed by caption partitioning"
                )
            # This is a presentation boundary, never a new phonetic estimate.
            boundaries.append((word_end + word_start) / 2)
        boundaries.append(right)
        for index, start, end in zip(members, boundaries, boundaries[1:]):
            segments[index].update(start=start, end=end)


def _align_transcription(
    args: Namespace,
    inputs: list[pathlib.Path],
    segments: list[dict[str, Any]],
    sample_rates: dict[str, int],
) -> dict[str, Any]:
    """Use unchanged native CTC estimates; diagnostics never adjust boundaries."""
    concerns: list[dict[str, Any]] = []
    diagnostics: list[dict[str, Any]] = []
    sources: list[dict[str, Any]] = []
    if any(segment["source"] not in sample_rates for segment in segments):
        raise tjm_v11.ValidationError("Alignment received an unknown source")
    with tempfile.TemporaryDirectory() as directory:
        for number, media in enumerate(inputs, 1):
            source = f"clip{number:02d}"
            selected = [segment for segment in segments if segment["source"] == source]
            words = [word for segment in selected for word in segment.get("words", [])]
            tokens = [word.get("token") for word in words]
            if not tokens or any(
                not isinstance(token, str) or not token.strip() for token in tokens
            ):
                raise tjm_v11.ValidationError(
                    f"{source}: no recognized tokens to align"
                )
            audio = pathlib.Path(directory) / f"{source}.wav"
            _emit_progress(args, "align", source=source, engine="ctc")
            ffmpeg_extract(media, audio)
            with wave.open(str(audio), "rb") as stream:
                duration = Fraction(stream.getnframes(), stream.getframerate())
            inference_metadata: dict[str, Any] = {}
            try:
                predicted = alignment.align_ctc(
                    audio,
                    tokens,
                    offline=os.environ.get("HF_HUB_OFFLINE") == "1",
                    inference_metadata=inference_metadata,
                )
            except ImportError as error:
                _raise_missing_backend_dependency(
                    "ctc alignment",
                    extra="alignment",
                    modules=("torch", "torchaudio", "transformers", "num2words"),
                    exc=error,
                )
            except (alignment.AlignmentError, RuntimeError, OSError) as error:
                raise tjm_v11.ValidationError(
                    f"{source}: acoustic alignment failed: {error}"
                ) from error
            if len(predicted) != len(words):
                raise tjm_v11.ValidationError(
                    f"{source}: alignment changed token count"
                )
            # Keep the recognizer's selected media envelopes: highlighting must
            # not truncate audio just because CTC stops before a spoken tail.
            envelopes: list[tuple[Fraction, Fraction] | None] = []
            for segment in selected:
                try:
                    left, right = (
                        Fraction(str(segment.get("start"))),
                        Fraction(str(segment.get("end"))),
                    )
                    first = Fraction(str(segment["words"][0].get("start")))
                    last = Fraction(str(segment["words"][-1].get("end")))
                    valid = 0 <= left <= first < last <= right <= duration
                    envelopes.append((left, right) if valid else None)
                except (ValueError, ZeroDivisionError):
                    envelopes.append(None)
            previous_end = Fraction(0)
            for index, (word, prediction) in enumerate(zip(words, predicted)):
                label = f"{source}, token {index} {word['token']!r}"
                if prediction.index != index or prediction.text != word["token"]:
                    raise tjm_v11.ValidationError(
                        f"{label}: alignment changed token identity or order"
                    )
                try:
                    start, end = (
                        Fraction(str(prediction.start)),
                        Fraction(str(prediction.end)),
                    )
                except (ValueError, ZeroDivisionError) as error:
                    raise tjm_v11.ValidationError(
                        f"{label}: invalid aligned timing"
                    ) from error
                _transcription_interval(
                    {"start": start, "end": end}, sample_rates[source], label
                )
                if start < previous_end or end > duration:
                    raise tjm_v11.ValidationError(
                        f"{label}: overlapping or out-of-source aligned timing"
                    )
                previous_end = end
                recognizer_start, recognizer_end = word.get("start"), word.get("end")
                try:
                    asr_start, asr_end = (
                        Fraction(str(recognizer_start)),
                        Fraction(str(recognizer_end)),
                    )
                    if asr_start < 0 or asr_end <= asr_start or asr_end > duration:
                        raise ValueError("invalid recognizer interval")
                except (ValueError, ZeroDivisionError):
                    diagnostics.append(
                        {
                            "code": "recognizer_timing_unavailable",
                            "blocking": False,
                            "source": source,
                            "word_index": index,
                            # Strings retain even NaN/Inf without invalid JSON.
                            "recognizer_start_seconds": (
                                None
                                if recognizer_start is None
                                else str(recognizer_start)
                            ),
                            "recognizer_end_seconds": (
                                None if recognizer_end is None else str(recognizer_end)
                            ),
                            "message": (
                                f"{label}: recognizer timing unavailable "
                                "for independent comparison"
                            ),
                        }
                    )
                else:
                    for boundary, recognized, aligned in (
                        ("start", asr_start, start),
                        ("end", asr_end, end),
                    ):
                        difference = abs(recognized - aligned)
                        if difference > Fraction(4, 25):
                            diagnostics.append(
                                {
                                    "code": "boundary_disagreement",
                                    "blocking": False,
                                    "source": source,
                                    "word_index": index,
                                    "boundary": boundary,
                                    "recognizer_seconds": str(recognized),
                                    "alignment_seconds": str(aligned),
                                    "difference_ms": float(difference * 1000),
                                    "message": (
                                        f"{label}: {boundary} estimates disagree by "
                                        f"{float(difference * 1000):.1f} ms (>160 ms)"
                                    ),
                                }
                            )
                word.update(start=start, end=end)
            source_record = {
                "source": source,
                "word_count": len(words),
                "inference": inference_metadata,
                "pause_diagnostic": alignment.pause_diagnostics(audio, predicted),
            }
            readings = [
                {
                    "word_index": item.index,
                    "text": item.text,
                    "alignment_text": item.alignment_text,
                }
                for item in predicted
                if getattr(item, "alignment_text", None) is not None
            ]
            if readings:
                source_record["normalization"] = {
                    "library": "num2words",
                    "version": package_version("num2words"),
                    "selection": "native_ctc_path_score_uncalibrated",
                    "max_reading_paths": alignment.MAX_NUMERIC_READING_PATHS,
                    "readings": readings,
                }
            sources.append(source_record)
            for segment, envelope in zip(selected, envelopes):
                start, end = segment["words"][0]["start"], segment["words"][-1]["end"]
                segment["start"] = min(envelope[0], start) if envelope else start
                segment["end"] = max(envelope[1], end) if envelope else end
            _partition_caption_envelopes(selected)
    return {
        "engine": "ctc",
        "model": alignment.CTC_MODEL,
        "revision": alignment.CTC_REVISION,
        "qualification": "experimental",
        "quality_concerns": concerns,
        "diagnostics": diagnostics,
        "forced": bool(getattr(args, "force", False) and concerns),
        "sources": sources,
        "notice": (
            "Runtime checks do not certify 95% of boundaries within 80 ms; "
            "backend qualification remains open."
        ),
    }


def _load_whisper_model() -> Any:
    module = importlib.import_module("faster_whisper")
    return module.WhisperModel


def _raise_missing_backend_dependency(
    backend: str,
    *,
    modules: tuple[str, ...],
    extra: str,
    exc: ImportError,
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
            _emit_progress(
                args, "extract", current=index, total=len(inputs), file=str(media_path)
            )
            ffmpeg_extract(media_path, wav_path)

            transcribe_kwargs: dict[str, Any] = {
                "beam_size": args.beam_size,
                "language": args.language,
                "vad_filter": getattr(args, "vad", False) or args.verbatim,
                "word_timestamps": True,
            }
            hotwords = args.hotwords
            if args.verbatim and hotwords is None:
                hotwords = DEFAULT_FILLER_HOTWORDS
            if hotwords:
                transcribe_kwargs["hotwords"] = hotwords
            if args.verbatim:
                transcribe_kwargs.update(
                    initial_prompt=VERBATIM_INITIAL_PROMPT,
                    condition_on_previous_text=False,
                    temperature=0.0,
                )

            _emit_progress(
                args, "decode", current=index, total=len(inputs), file=str(media_path)
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
            _emit_progress(
                args, "extract", current=index, total=len(inputs), file=str(media_path)
            )
            ffmpeg_extract(media_path, wav_path)

            inference_kwargs: dict[str, Any] = {"return_timestamps": "word"}
            generate_kwargs = _build_transformers_generate_kwargs(asr_pipeline, args)
            if generate_kwargs:
                inference_kwargs["generate_kwargs"] = generate_kwargs

            _emit_progress(
                args, "decode", current=index, total=len(inputs), file=str(media_path)
            )
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
            _emit_progress(
                args, "extract", current=index, total=len(inputs), file=str(media_path)
            )
            ffmpeg_extract(media_path, wav_path)

            _emit_progress(
                args, "decode", current=index, total=len(inputs), file=str(media_path)
            )
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
        default="medium.en",
        help="Whisper model name/path (default: medium.en)",
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
        "--max-segment-words",
        type=int,
        default=8,
        help=(
            "Target maximum words per segment; segments are split at sentence "
            "and clause boundaries. Set to 0 to disable. (default: 8)"
        ),
    )
    parser.add_argument(
        "--tag-fillers",
        action="store_true",
        help="Tag filler words in the output manifest before writing",
    )
    parser.add_argument(
        "--verbatim",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Bias decoding toward verbatim disfluencies on supported backends",
    )
    parser.add_argument(
        "--hotwords",
        default=None,
        help=(
            "Hotwords passed to supported backends. In verbatim faster-whisper mode, "
            "defaults to common filler/disfluency words."
        ),
    )
    parser.add_argument(
        "--vad",
        action="store_true",
        help="Enable voice-activity-detection filtering (faster-whisper only)",
    )
    parser.add_argument(
        "--progress-json",
        action="store_true",
        help="Emit machine-readable progress events as JSON lines on stderr",
    )
    parser.add_argument(
        "--word-aligner",
        choices=("none", "ctc"),
        default="none",
        help="Opt-in native CTC alignment (English words and unsigned integers; "
        "experimental)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Override recorded word-alignment quality concerns, never invalid timing",
    )
    parser.add_argument(
        "--alignment-report",
        help="Alignment decision JSON (default: OUTPUT.alignment-report.json)",
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
    output_path = pathlib.Path(args.output).expanduser()
    align_words = getattr(args, "word_aligner", "none") == "ctc"
    if (
        getattr(args, "force", False) or getattr(args, "alignment_report", None)
    ) and not align_words:
        raise tjm_v11.ValidationError(
            "--force and --alignment-report require --word-aligner ctc"
        )
    if align_words and (
        args.backend != "faster-whisper" or args.language != "en" or stub_mode
    ):
        raise tjm_v11.ValidationError(
            "CTC alignment requires real faster-whisper English recognition"
        )
    protected = {path.resolve() for path in inputs}
    if output_path.resolve() in protected:
        raise tjm_v11.ValidationError("Manifest output must not overwrite input media")
    report_path = pathlib.Path(
        getattr(args, "alignment_report", None)
        or f"{output_path}.alignment-report.json"
    ).expanduser()
    if align_words and report_path.resolve() in protected | {output_path.resolve()}:
        raise tjm_v11.ValidationError(
            "Alignment report must not overwrite media or manifest"
        )
    sample_rates = {
        f"clip{index:02d}": probe_audio_sample_rate(media_path)
        for index, media_path in enumerate(inputs, start=1)
    }
    manifest_sources = [
        {
            "id": f"clip{index:02d}",
            "file": os.path.relpath(media_path.resolve(), output_path.resolve().parent),
            "timebase": {
                "numerator": 1,
                "denominator": sample_rates[f"clip{index:02d}"],
            },
        }
        for index, media_path in enumerate(inputs, start=1)
    ]
    manifest_segments: list[dict[str, Any]] = []

    alignment_record: dict[str, Any] | None = None
    backend_runners: dict[str, BackendRunner] = {
        "faster-whisper": transcribe_faster_whisper,
        "transformers": transcribe_transformers,
        "nemo": transcribe_nemo,
    }
    if not stub_mode:
        try:
            _emit_progress(
                args, "start", total=len(inputs), backend=args.backend, model=args.model
            )
            _emit_progress(args, "load-model", backend=args.backend, model=args.model)
            manifest_segments = backend_runners[args.backend](args, inputs)
            if args.max_segment_words > 0:
                _emit_progress(args, "resegment", max_words=args.max_segment_words)
                manifest_segments = resegment_naturally(
                    manifest_segments, args.max_segment_words
                )
            if align_words:
                alignment_record = _align_transcription(
                    args, inputs, manifest_segments, sample_rates
                )
        except MissingBackendDependencyError as exc:
            print(f"eve transcribe: {exc}", file=sys.stderr)
            return 2

    if args.tag_fillers:
        _emit_progress(args, "tag-fillers")
        tag_manifest_fillers({"segments": manifest_segments}, DEFAULT_FILLERS)
    manifest = {
        "version": "1.1",
        "sources": manifest_sources,
        "segments": _v11_segments(manifest_segments, sample_rates),
        "render": {"filler_policy": "keep", "preserve_short_gaps": 0.0},
    }
    if alignment_record is not None:
        manifest["alignment"] = alignment_record
    tjm_v11.validate_manifest(manifest)
    if alignment_record is not None:
        permitted = (
            not alignment_record["quality_concerns"] or alignment_record["forced"]
        )
        write_json_atomic(
            report_path,
            {
                "manifest": str(output_path),
                "publication_permitted": bool(permitted),
                "alignment": alignment_record,
            },
        )
        if not permitted:
            raise tjm_v11.ValidationError(
                f"Word-alignment quality concerns "
                f"({len(alignment_record['quality_concerns'])}); see {report_path}. "
                "Use --force to accept unchanged valid estimates."
            )
        print(f"[eve transcribe] NOTICE: {alignment_record['notice']}", file=sys.stderr)
        if alignment_record["forced"]:
            print(
                f"[eve transcribe] WARNING: alignment quality override "
                f"recorded in {report_path}",
                file=sys.stderr,
            )
    _emit_progress(args, "write", output=str(output_path))
    write_json_atomic(output_path, manifest, pretty=args.pretty)

    _emit_progress(
        args, "done", output=str(output_path), segments=len(manifest_segments)
    )
    return 0
