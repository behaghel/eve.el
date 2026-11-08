"""Audio-only consumer of the existing exact TJM render timeline."""

from __future__ import annotations

import json
import pathlib
import subprocess
import tempfile
import wave
from argparse import Namespace
from dataclasses import dataclass
from fractions import Fraction
from typing import Any

from .. import tjm_v11
from . import text_edit
from .common import write_json_atomic


@dataclass(frozen=True)
class AudioRange:
    source: pathlib.Path
    start: Fraction
    end: Fraction


@dataclass(frozen=True)
class AudioSource:
    rate: int
    channels: int
    origin: Fraction

    def sample(self, time: Fraction) -> int:
        value = (time - self.origin) * self.rate
        if value.denominator != 1 or value < 0:
            raise tjm_v11.ValidationError(
                "Audio cut is outside the source sample grid or precedes its audio"
            )
        return int(value)


def plan(manifest: dict[str, Any]) -> list[AudioRange]:
    """Coalesce contiguous source coverage, not captions, into audio pieces."""
    if manifest.get("version") != "1.1":
        raise tjm_v11.ValidationError("Audio-only rendering requires TJM v1.1")
    if "subtitles" in manifest:
        raise tjm_v11.ValidationError(
            "Audio-only output cannot burn subtitles; use --subtitles for WebVTT"
        )
    for segment in manifest["segments"]:
        if not text_edit.edit_deleted(segment) and text_edit.normalized_segment_broll(
            segment
        ):
            raise tjm_v11.ValidationError("Audio-only output does not support b-roll")
    sources = {s["id"]: pathlib.Path(s["file"]) for s in manifest["sources"]}
    result: list[AudioRange] = []
    for segment, _, _, ranges, _ in text_edit._subtitle_timeline(
        manifest, manifest["render"]["preserve_short_gaps"], include_non_captions=True
    ):
        for start, end in ranges:
            current = AudioRange(sources[segment["source"]], start, end)
            if (
                result
                and result[-1].source == current.source
                and result[-1].end == start
            ):
                result[-1] = AudioRange(current.source, result[-1].start, end)
            else:
                result.append(current)
    if not result:
        raise tjm_v11.ValidationError("No retained audio to render")
    return result


def probe(source: pathlib.Path) -> AudioSource:
    if not source.is_file():
        raise tjm_v11.ValidationError(f"Audio source not found: {source}")
    raw = subprocess.check_output(
        [
            text_edit.FFPROBE,
            "-v",
            "error",
            "-select_streams",
            "a:0",
            "-show_entries",
            "stream=sample_rate,channels,start_pts,time_base",
            "-of",
            "json",
            str(source),
        ]
    )
    streams = json.loads(raw)["streams"]
    if not streams:
        raise tjm_v11.ValidationError(f"No audio stream in source: {source}")
    stream = streams[0]
    origin = Fraction(stream.get("start_pts", 0)) * Fraction(stream["time_base"])
    return AudioSource(int(stream["sample_rate"]), int(stream["channels"]), origin)


def render_pieces(
    intervals: list[AudioRange],
    inputs: dict[pathlib.Path, AudioSource],
    working: pathlib.Path,
) -> list[pathlib.Path]:
    """Render sample-count-checked lossless pieces before final AAC encoding."""
    decoded: dict[pathlib.Path, pathlib.Path] = {}
    lengths: dict[pathlib.Path, int] = {}
    for index, (path, info) in enumerate(inputs.items()):
        dest = working / f"source-{index}.wav"
        text_edit.run_ffmpeg(
            [
                text_edit.FFMPEG,
                "-v",
                "error",
                "-nostdin",
                "-y",
                "-i",
                str(path),
                "-map",
                "0:a:0",
                "-vn",
                "-c:a",
                "pcm_s32le",
                str(dest),
            ],
            context=f"decoding audio source {path}",
        )
        with wave.open(str(dest)) as stream:
            lengths[path] = stream.getnframes()
            if (stream.getframerate(), stream.getnchannels()) != (
                info.rate,
                info.channels,
            ):
                raise tjm_v11.ValidationError(
                    "Decoded audio format differs from source metadata"
                )
        decoded[path] = dest
    parts = []
    for index, interval in enumerate(intervals):
        info = inputs[interval.source]
        start, end = info.sample(interval.start), info.sample(interval.end)
        if end > lengths[interval.source]:
            raise tjm_v11.ValidationError(
                f"Audio cut exceeds decoded source: {interval.source}"
            )
        length = end - start
        dest = working / f"piece-{index}.wav"
        filters = f"atrim=end_sample={length},asetpts=PTS-STARTPTS"
        filters += "," + str(text_edit.audio_edge_fade_filter(length / info.rate))
        print(
            f"[eve text-edit] Rendering {index + 1}/{len(intervals)}: audio", flush=True
        )
        text_edit.run_ffmpeg(
            [
                text_edit.FFMPEG,
                "-v",
                "error",
                "-nostdin",
                "-y",
                "-ss",
                f"{start / info.rate:.9f}",
                "-i",
                str(decoded[interval.source]),
                "-map",
                "0:a:0",
                "-af",
                filters,
                "-c:a",
                "pcm_s32le",
                str(dest),
            ],
            context="rendering an audio interval",
        )
        with wave.open(str(dest)) as stream:
            if stream.getnframes() != length:
                raise tjm_v11.ValidationError(
                    "Rendered audio interval has an unexpected sample count"
                )
        parts.append(dest)
    return parts


def run(
    args: Namespace,
    manifest: dict[str, Any],
    original: dict[str, Any],
    report: pathlib.Path | None,
) -> int:
    """Render lossless pieces and encode the assembled audio only once."""
    output = pathlib.Path(args.output).expanduser()
    vtt = (
        None
        if args.subtitles is None
        else (
            output.with_suffix(".vtt")
            if args.subtitles == ""
            else pathlib.Path(args.subtitles).expanduser()
        )
    )
    pretty = (
        pathlib.Path(args.pretty_manifest).expanduser()
        if args.pretty_manifest
        else None
    )
    protected = {pathlib.Path(args.manifest).resolve()}
    protected.update(pathlib.Path(s["file"]).resolve() for s in manifest["sources"])
    destinations = [p.resolve() for p in [output, vtt, pretty, report] if p is not None]
    if len(set(destinations)) != len(destinations) or protected.intersection(
        destinations
    ):
        raise tjm_v11.ValidationError(
            "Render outputs must not overwrite sources, input manifest or one another"
        )
    result: dict[str, Any]
    try:
        intervals = plan(manifest)
        inputs = {
            source: probe(source)
            for source in dict.fromkeys(item.source for item in intervals)
        }
        format_set = {(s.rate, s.channels) for s in inputs.values()}
        if len(format_set) != 1:
            raise tjm_v11.ValidationError(
                "Audio sources must have matching sample rates and channel counts"
            )
        for item in intervals:
            inputs[item.source].sample(item.start)
            inputs[item.source].sample(item.end)
        if getattr(args, "segments", None) or getattr(args, "partial_every", 0):
            raise tjm_v11.ValidationError(
                "Audio-only output does not support cache warming or partial renders"
            )
    except tjm_v11.ValidationError as exc:
        if not getattr(args, "validate", False):
            raise
        result = {"valid": False, "errors": [{"message": str(exc)}], "warnings": []}
    else:
        result = {"valid": True, "errors": [], "warnings": []}
    if getattr(args, "validate", False):
        if args.json:
            print(json.dumps(result))
        else:
            print(
                "[eve text-edit] "
                + (
                    "Manifest valid."
                    if result["valid"]
                    else result["errors"][0]["message"]
                )
            )
        return 0 if result["valid"] else 1
    if getattr(args, "dry_run", False):
        count = len(intervals)
        result = {
            "total_segments": count,
            "changed_segments": count,
            "cached_segments": 0,
            "cache_hit_rate": 0.0,
            "changed_ids": [
                s["id"] for s in manifest["segments"] if not text_edit.edit_deleted(s)
            ],
            "estimated_seconds": None,
        }
        print(
            json.dumps(result)
            if args.json
            else f"[eve text-edit] {count} audio pieces to render (uncached)"
        )
        return 0
    output.parent.mkdir(parents=True, exist_ok=True)
    rate = next(iter(inputs.values())).rate
    frames_expected = int(sum((p.end - p.start for p in intervals), Fraction(0)) * rate)
    with tempfile.TemporaryDirectory(dir=args.workdir) as directory:
        working = pathlib.Path(directory)
        parts = render_pieces(intervals, inputs, working)
        # Assemble raw PCM on one sample clock: concat-demuxer durations are
        # microsecond-rounded and must not accumulate across many short cuts.
        timeline = working / "timeline.pcm"
        with timeline.open("wb") as destination:
            for part in parts:
                with wave.open(str(part)) as source:
                    while block := source.readframes(65536):
                        destination.write(block)
        channels = next(iter(inputs.values())).channels
        if timeline.stat().st_size != frames_expected * channels * 4:
            raise tjm_v11.ValidationError(
                "Assembled audio sample count differs from its timeline"
            )
        # Stage next to the destination: an encoder failure cannot replace it.
        with tempfile.TemporaryDirectory(dir=output.parent) as stage_dir:
            stage = pathlib.Path(stage_dir)
            staged_audio = stage / "audio.m4a"
            codec = [
                "-c:a",
                "aac",
                "-b:a",
                "192k",
                "-movflags",
                "+faststart",
                "-movie_timescale",
                str(rate),
            ]
            text_edit.run_ffmpeg(
                [
                    text_edit.FFMPEG,
                    "-v",
                    "error",
                    "-nostdin",
                    "-y",
                    "-f",
                    "s32le",
                    "-ar",
                    str(rate),
                    "-ac",
                    str(channels),
                    "-i",
                    str(timeline),
                    "-map",
                    "0:a:0",
                    *codec,
                    str(staged_audio),
                ],
                context="encoding assembled audio",
            )
            if vtt is not None:
                text_edit.write_webvtt(
                    text_edit.build_subtitle_cues(
                        manifest, manifest["render"]["preserve_short_gaps"]
                    ),
                    stage / "captions.vtt",
                )
                vtt.parent.mkdir(parents=True, exist_ok=True)
            staged_audio.replace(output)
            if vtt is not None:
                (stage / "captions.vtt").replace(vtt)
    if pretty is not None:
        text_edit.write_manifest(original, pretty)
    if report is not None:
        write_json_atomic(
            report,
            {
                "manifest": args.manifest,
                "output": str(output),
                "alignment": original.get("alignment"),
                "media_type": "audio",
                "sample_rate": rate,
                "samples": frames_expected,
            },
        )
    print(f"[eve text-edit] Final cut available at {output}", flush=True)
    return 0
