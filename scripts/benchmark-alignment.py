#!/usr/bin/env python3
"""Opt-in local alignment experiment; never promotes a candidate to production."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import tempfile
import time
from fractions import Fraction
from pathlib import Path
from typing import Any

from eve_cli import alignment
from eve_cli.commands import transcribe
from eve_cli.main import main as eve_main


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("reference", type=Path, help="Reviewed v1.1 word intervals")
    parser.add_argument("--audio", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--engine",
        choices=["ctc", "ctc-boundary", "ctc-phoneme", "qwen", "mfa"],
        default="ctc",
    )
    parser.add_argument(
        "--frame-times", type=Path, help="Native video PTS JSON (time_base, pts)"
    )
    parser.add_argument(
        "--pronunciations",
        type=Path,
        help="Explicit single-token ARPAbet overrides for MFA experiments",
    )
    parser.add_argument(
        "--speaker-adaptation",
        action="store_true",
        help="Use MFA's normal speaker-adaptation pass",
    )
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--render", action="store_true")
    args = parser.parse_args()
    if args.pronunciations is not None and args.engine not in ("mfa", "ctc-phoneme"):
        parser.error("Pronunciation overrides require an MFA-based experiment")
    if args.speaker_adaptation and args.engine != "mfa":
        parser.error("Speaker adaptation is available for the supported MFA pipeline")
    reference_manifest = json.loads(args.reference.read_text())
    sources = reference_manifest["sources"]
    if len(sources) != 1:
        parser.error("The initial benchmark requires one source")
    source = sources[0]
    frame_times = None
    if args.frame_times is not None:
        video = json.loads(args.frame_times.read_text())
        if not all(type(point) is int for point in video["pts"]):
            parser.error("Video PTS must be native integer ticks")
        frame_times = [point * Fraction(video["time_base"]) for point in video["pts"]]
    base = Fraction(source["timebase"]["numerator"], source["timebase"]["denominator"])
    original = [
        word for segment in reference_manifest["segments"] for word in segment["words"]
    ]
    reference = [
        alignment.WordAlignment(
            index, word["spoken"], word["start_tick"] * base, word["end_tick"] * base
        )
        for index, word in enumerate(original)
    ]
    words = [word.text for word in reference]
    engines = {
        "ctc": (alignment.align_ctc, alignment.CTC_MODEL, alignment.CTC_REVISION),
        "ctc-boundary": (
            alignment.align_ctc_boundary,
            alignment.CTC_MODEL,
            alignment.CTC_REVISION,
        ),
        "ctc-phoneme": (
            alignment.align_ctc_phoneme,
            alignment.CTC_MODEL + "+" + alignment.MFA_MODEL,
            alignment.CTC_REVISION + "+" + alignment.MFA_REVISION,
        ),
        "qwen": (alignment.align_qwen, alignment.QWEN_MODEL, alignment.QWEN_REVISION),
        "mfa": (alignment.align_mfa, alignment.MFA_MODEL, alignment.MFA_REVISION),
    }
    aligner, model_name, revision = engines[args.engine]
    experiment = {}
    alignment_options: dict[str, Any] = {"offline": args.offline}
    inference_metadata: dict[str, Any] = {}
    if args.engine in ("ctc", "ctc-boundary"):
        alignment_options["inference_metadata"] = inference_metadata
    if args.speaker_adaptation:
        alignment_options["speaker_adaptation"] = True
    if args.pronunciations is not None:
        alignment_options["pronunciations"] = args.pronunciations
        experiment["pronunciation_override"] = {
            "file": str(args.pronunciations.resolve()),
            "sha256": hashlib.sha256(args.pronunciations.read_bytes()).hexdigest(),
        }
    if args.engine == "ctc-phoneme":
        experiment["local_phoneme_configuration"] = {
            "neighbors_each_side": 1,
            "context_margin_ms": 200,
            "edge_guard": "one native phoneme frame",
            "anchor_slack_ms": 20,
            "implementation_sha256": hashlib.sha256(
                Path(alignment.__file__).read_bytes()
            ).hexdigest(),
            "native_extractor_sha256": hashlib.sha256(
                (
                    Path(__file__).resolve().parent
                    / "alignment-mfa"
                    / "extract-frames.py"
                ).read_bytes()
            ).hexdigest(),
        }
    args.output_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="eve-alignment-") as temporary:
        audio = Path(temporary) / "audio.wav"
        transcribe.ffmpeg_extract(args.audio, audio)
        started = time.monotonic()
        try:
            predicted = aligner(audio, words, **alignment_options)
        except alignment.AlignmentError as error:
            # Invalid predictions have no measurable boundary accuracy. Do not
            # turn a rejection into synthetic intervals or a made-up 0% score.
            report = {
                "status": "alignment_rejected",
                "pass": False,
                "reason": str(error),
                "engine": args.engine,
                "model": model_name,
                "revision": revision,
                "reference": str(args.reference.resolve()),
                "audio": str(args.audio.resolve()),
                "reference_word_count": len(reference),
                "first_call_seconds": time.monotonic() - started,
                "production_eligible": False,
                "unspoken_word_probe": {"status": "not_run"},
            }
            report.update(experiment)
            if error.diagnostics:
                report["diagnostics"] = error.diagnostics
            (args.output_dir / "report.json").write_text(
                json.dumps(report, indent=2) + "\n"
            )
            print(json.dumps(report, indent=2))
            return 1
        elapsed = time.monotonic() - started
        report = alignment.evaluate(reference, predicted, frame_times=frame_times)
        report["timing_pass"] = report["pass"]
        if inference_metadata:
            report["inference"] = copy.deepcopy(inference_metadata)
        report.update(experiment)
        report.update(
            {
                "engine": args.engine,
                "model": model_name,
                "revision": revision,
                "first_call_seconds": elapsed,
                "reference": str(args.reference.resolve()),
                "audio": str(args.audio.resolve()),
                "reference_word_count": len(reference),
                "production_eligible": False,
            }
        )
        if args.engine == "ctc-boundary":
            report["ctc_refinement"] = {
                "algorithm": "delimiter_end_then_quiet_supported_edges",
                "implementation_sha256": hashlib.sha256(
                    Path(alignment.__file__).read_bytes()
                ).hexdigest(),
            }
        if args.engine in ("mfa", "ctc-phoneme"):
            lock = Path(__file__).resolve().parent / "alignment-mfa" / "pixi.lock"
            report["mfa_configuration"] = {
                "environment_lock_sha256": hashlib.sha256(
                    lock.read_bytes()
                ).hexdigest(),
                "speaker_adaptation": args.speaker_adaptation,
                "pronunciations": (
                    "explicit overrides plus bundled dictionary and G2P"
                    if args.pronunciations is not None
                    else "bundled dictionary plus matching G2P"
                ),
                "timing_source": "native integer lattice frames",
                "timestamp_postprocessing": False,
            }
        try:
            negative = aligner(audio, words + ["know"], **alignment_options)
            extra = negative[-1]
            report["unspoken_word_probe"] = {
                "text": extra.text,
                "rejected": False,
                "start": float(extra.start),
                "end": float(extra.end),
                "acoustic_score_uncalibrated": extra.acoustic_score,
                "note": "A positive forced interval does not prove a word was spoken",
            }
        except alignment.AlignmentError as error:
            report["unspoken_word_probe"] = {
                "text": "know",
                "rejected": True,
                "reason": str(error),
            }
    report["pass"] = report["timing_pass"] and report["unspoken_word_probe"]["rejected"]
    (args.output_dir / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(
        json.dumps(
            {key: value for key, value in report.items() if key != "words"}, indent=2
        )
    )
    if args.render:
        candidate = copy.deepcopy(reference_manifest)
        file = Path(source["file"])
        candidate["sources"][0]["file"] = str(
            file if file.is_absolute() else args.reference.resolve().parent / file
        )
        aligned_words = []
        for raw, result in zip(original, predicted):
            item = {
                "spoken": raw["spoken"],
                "start_tick": round(result.start / base),
                "end_tick": round(result.end / base),
            }
            if "display" in raw:
                item["display"] = raw["display"]
            aligned_words.append(item)
        candidate["segments"] = [
            {
                "id": "candidate-not-approved",
                "source": source["id"],
                "start_tick": 0,
                "end_tick": max(
                    segment["end_tick"] for segment in reference_manifest["segments"]
                ),
                "words": aligned_words,
                "spoken_text": " ".join(word["spoken"] for word in aligned_words),
                "display_text": " ".join(
                    word.get("display", word["spoken"]) for word in aligned_words
                ),
            }
        ]
        path = args.output_dir / "candidate-not-approved.tjm.json"
        path.write_text(json.dumps(candidate, indent=2) + "\n")
        status = eve_main(
            [
                "text-edit",
                str(path),
                "--output",
                str(args.output_dir / "candidate-not-approved.mp4"),
                "--quality",
                "final",
                "--no-cache",
            ]
        )
        if status:
            return status
    return 0 if report["pass"] and report["unspoken_word_probe"]["rejected"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
