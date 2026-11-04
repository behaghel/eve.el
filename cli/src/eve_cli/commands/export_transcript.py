"""Export reviewed text on one original source clock, without rendering media."""

from __future__ import annotations

import html
import json
import os
import tempfile
from argparse import ArgumentParser, Namespace, _SubParsersAction
from fractions import Fraction
from pathlib import Path
from typing import Any, cast

from eve_cli import tjm_v11

from .common import add_json_flag


def register(subparsers: _SubParsersAction[ArgumentParser]) -> None:
    parser = subparsers.add_parser(
        "export-transcript", help="Export source-clock WebVTT or plain text; no render."
    )
    parser.add_argument("manifest", help="Reviewed TJM v1.1 manifest")
    parser.add_argument("--output", required=True, help="Destination text file")
    parser.add_argument("--source", help="Source ID; optional for a single-source TJM")
    parser.add_argument("--format", choices=("vtt", "text"), default="vtt")
    add_json_flag(parser)
    parser.set_defaults(handler=run, command="export-transcript")


def _text(segment: dict[str, Any]) -> str:
    words = segment.get("words") or []
    for field in ("display_text", "spoken_text"):
        if field in segment and not isinstance(segment[field], str):
            raise tjm_v11.ValidationError(
                f"Segment {segment['id']!r}: {field} must be text"
            )
    if any(word.get("edit", {}).get("deleted") for word in words):
        raise tjm_v11.ValidationError("Source export does not apply word deletions")
    text = (
        segment.get("display_text")
        or " ".join(word.get("display") or word["spoken"] for word in words)
        or segment.get("spoken_text", "")
    )
    text = " ".join(text.split())
    if not text:
        raise tjm_v11.ValidationError(
            f"Segment {segment['id']!r} has no transcript text"
        )
    return text


def _timestamp(milliseconds: int) -> str:
    hours, remainder = divmod(milliseconds, 3600000)
    minutes, remainder = divmod(remainder, 60000)
    seconds, fraction = divmod(remainder, 1000)
    return f"{hours:02}:{minutes:02}:{seconds:02}.{fraction:03}"


def _publish(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, delete=False
        ) as stream:
            temporary = Path(stream.name)
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def run(args: Namespace) -> int:
    manifest_path = Path(args.manifest).expanduser().resolve()
    output = Path(args.output).expanduser().resolve()
    try:
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeError) as error:
        raise tjm_v11.ValidationError(f"Invalid TJM JSON: {error}") from error
    manifest = cast(dict[str, Any], tjm_v11.parse_manifest(raw))
    sources = manifest["sources"]
    protected = {manifest_path} | {
        (manifest_path.parent / source["file"]).resolve() for source in sources
    }
    if output in protected:
        raise tjm_v11.ValidationError("Export must not overwrite the manifest or media")
    selected = args.source
    if selected is None:
        if len(sources) != 1:
            raise tjm_v11.ValidationError("Select one original source with --source ID")
        selected = sources[0]["id"]
    source = next((item for item in sources if item["id"] == selected), None)
    if source is None:
        raise tjm_v11.ValidationError(f"Unknown source ID {selected!r}")
    if manifest["render"].get("filler_policy") != "keep":
        raise tjm_v11.ValidationError("Source export requires filler_policy 'keep'")
    timebase = source["timebase"]
    tick = Fraction(timebase["numerator"], timebase["denominator"])
    segments = sorted(
        (
            item
            for item in manifest["segments"]
            if item.get("kind") != "marker" and item["source"] == selected
        ),
        key=lambda item: item["start_tick"],
    )
    if not segments:
        raise tjm_v11.ValidationError("Selected source has no transcript segments")
    cues: list[tuple[int, int, str]] = []
    previous_end = Fraction(0)
    for segment in segments:
        edit = segment.get("edit", {})
        if edit.get("deleted") or edit.get("cuts"):
            raise tjm_v11.ValidationError(
                "Source export does not apply media cuts/deletions"
            )
        start, end = segment["start_tick"] * tick, segment["end_tick"] * tick
        if start < previous_end:
            raise tjm_v11.ValidationError(
                "Overlapping or negative source caption timing"
            )
        previous_end = end
        text = _text(segment)
        start_ms, end_ms = round(start * 1000), round(end * 1000)
        if args.format == "vtt" and end_ms <= start_ms:
            raise tjm_v11.ValidationError(
                "Caption collapses at WebVTT millisecond precision"
            )
        cues.append((start_ms, end_ms, text))
    if args.format == "text":
        result = "\n\n".join(text for _, _, text in cues) + "\n"
    else:
        result = "WEBVTT\n\n" + "".join(
            f"{index}\n{_timestamp(start)} --> {_timestamp(end)}\n"
            f"{html.escape(text, quote=False)}\n\n"
            for index, (start, end, text) in enumerate(cues, 1)
        )
    _publish(output, result)
    if args.json:
        print(
            json.dumps(
                {
                    "output": str(output),
                    "source": selected,
                    "format": args.format,
                    "cues": len(cues),
                }
            )
        )
    return 0
