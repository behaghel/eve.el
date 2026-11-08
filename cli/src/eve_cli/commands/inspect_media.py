"""Read-only source-stream inspection for an editor's output-container choice."""

from __future__ import annotations

import json
import pathlib
import subprocess
from argparse import ArgumentParser, Namespace, _SubParsersAction
from typing import Any

from .. import tjm_v11
from ..output import emit_success
from . import text_edit
from .common import add_json_flag


def register(subparsers: _SubParsersAction[ArgumentParser]) -> None:
    parser = subparsers.add_parser(
        "inspect-media",
        help="Inspect retained source streams without rendering or changing the TJM.",
    )
    parser.add_argument("manifest", help="Path to the TJM manifest")
    add_json_flag(parser)
    parser.set_defaults(handler=run, command="inspect-media")


def inspect(manifest_path: pathlib.Path) -> dict[str, Any]:
    original = text_edit.load_manifest(manifest_path)
    if original.get("version") not in ("1.1", 1, "1"):
        raise tjm_v11.ValidationError(
            f"Unsupported TJM version: {original.get('version')}"
        )
    manifest = text_edit.prepare_render_manifest(
        original, manifest_path.resolve().parent
    )
    used = set()
    visual = False
    for segment in manifest.get("segments", []):
        if text_edit.edit_deleted(segment):
            continue
        if text_edit.normalized_segment_broll(segment):
            visual = True
        if (
            segment.get("source") is not None
            and text_edit.segment_duration(segment) > 0
        ):
            used.add(segment["source"])
    known = {s["id"] for s in manifest.get("sources", [])}
    if used - known:
        raise tjm_v11.ValidationError("Unknown retained media source")
    sources = []
    for source in manifest.get("sources", []):
        if source["id"] not in used:
            continue
        path = pathlib.Path(source["file"]).expanduser()
        if not path.is_absolute():
            path = manifest_path.resolve().parent / path
        if not path.is_file():
            raise tjm_v11.ValidationError(f"Media source not found: {path}")
        try:
            raw = subprocess.check_output(
                [
                    text_edit.FFPROBE,
                    "-v",
                    "error",
                    "-show_entries",
                    "stream=codec_type:stream_disposition=attached_pic",
                    "-of",
                    "json",
                    str(path),
                ],
                stderr=subprocess.PIPE,
            )
        except subprocess.CalledProcessError as exc:
            raise tjm_v11.ValidationError(
                f"Cannot inspect media source: {path}"
            ) from exc
        streams = json.loads(raw)["streams"]
        has_audio = any(s.get("codec_type") == "audio" for s in streams)
        has_video = any(
            s.get("codec_type") == "video"
            and not s.get("disposition", {}).get("attached_pic")
            for s in streams
        )
        if not has_audio and not has_video:
            raise tjm_v11.ValidationError(f"No playable audio or video stream: {path}")
        sources.append(dict(id=source["id"], has_audio=has_audio, has_video=has_video))
        visual = visual or has_video
    if not sources and not visual:
        raise tjm_v11.ValidationError(
            "No retained media for automatic output selection"
        )
    return {"output_format": "mp4" if visual else "m4a", "sources": sources}


def run(args: Namespace) -> int:
    payload = inspect(pathlib.Path(args.manifest).expanduser())
    emit_success(
        args, {**payload, "message": f"Output format: {payload['output_format']}"}
    )
    return 0
