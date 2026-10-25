"""Experimental acoustic alignment and explicit accuracy benchmarking.

Scores are uncalibrated diagnostics, never probabilities that a word was spoken.
Native CTC is an explicit transcription opt-in; experimental status is not a
per-recording quality failure. Other engines remain benchmark-only.
"""

from __future__ import annotations

import importlib
import json
import math
import os
import re
import shutil
import subprocess
import tempfile
import unicodedata
import wave
from collections.abc import Sequence
from dataclasses import dataclass
from fractions import Fraction
from itertools import product
from pathlib import Path
from typing import Any

CTC_MODEL = "facebook/wav2vec2-base-960h"
CTC_REVISION = "22aad52d435eb6dbaf354bdad9b0da84ce7d6156"
QWEN_MODEL = "Qwen/Qwen3-ForcedAligner-0.6B"
QWEN_REVISION = "c7cbfc2048c462b0d63a45797104fc9db3ad62b7"
MFA_MODEL = "MontrealCorpusTools/english_us_arpa"
MFA_REVISION = "9c8d5d8e1dd5d9cf188a5571203de0e09ca6bede"
MAX_NUMERIC_READING_PATHS = 32
CTC_WINDOW_SECONDS = 10
CTC_CONTEXT_SECONDS = 2


class AlignmentError(ValueError):
    def __init__(self, message: str, *, diagnostics: dict[str, Any] | None = None):
        super().__init__(message)
        self.diagnostics = diagnostics or {}


@dataclass(frozen=True)
class WordAlignment:
    index: int
    text: str
    start: Fraction
    end: Fraction
    acoustic_score: float | None = None
    alignment_text: str | None = None


@dataclass(frozen=True)
class FrameClock:
    sample_rate: int
    hop_samples: int

    def interval(self, start: int, end: int) -> tuple[Fraction, Fraction]:
        if self.sample_rate <= 0 or self.hop_samples <= 0 or start < 0 or end <= start:
            raise AlignmentError("Invalid model-frame interval")
        return (
            Fraction(start * self.hop_samples, self.sample_rate),
            Fraction(end * self.hop_samples, self.sample_rate),
        )


def evaluate(
    reference: list[WordAlignment],
    predicted: list[WordAlignment],
    *,
    tolerance: Fraction = Fraction(2, 25),
    required_fraction: Fraction = Fraction(19, 20),
    stretch_tolerance: Fraction = Fraction(1, 25),
    frame_rate: Fraction = Fraction(60),
    frame_times: list[Fraction] | None = None,
) -> dict[str, Any]:
    if not reference or len(reference) != len(predicted):
        raise AlignmentError("Reference and prediction must contain the same tokens")
    errors: list[Fraction] = []
    details: list[dict[str, Any]] = []
    for expected, actual in zip(reference, predicted):
        if expected.index != actual.index or expected.text.casefold().strip(
            ".,!?;:"
        ) != actual.text.casefold().strip(".,!?;:"):
            raise AlignmentError("Alignment changed token identity or order")
        if expected.end <= expected.start or actual.end <= actual.start:
            raise AlignmentError("Non-positive token interval cannot pass evaluation")
        pair = [abs(expected.start - actual.start), abs(expected.end - actual.end)]
        errors.extend(pair)
        details.append(
            {
                "index": actual.index,
                "text": actual.text,
                "start_error_ms": float((actual.start - expected.start) * 1000),
                "end_error_ms": float((actual.end - expected.end) * 1000),
                "reference_start": float(expected.start),
                "reference_end": float(expected.end),
                "start": float(actual.start),
                "end": float(actual.end),
                "acoustic_score_uncalibrated": actual.acoustic_score,
            }
        )
    within = sum(error <= tolerance for error in errors)
    stretch_within = sum(error <= stretch_tolerance for error in errors)
    ordered = sorted(errors)
    p95 = ordered[math.ceil(len(ordered) * 0.95) - 1]
    max_time = max(word.end for word in reference + predicted)
    supplied_pts = frame_times is not None
    if frame_times is None:
        frame_times = [
            Fraction(index, 1) / frame_rate
            for index in range(math.ceil(max_time * frame_rate))
        ]
    if (
        not frame_times
        or frame_times[0] < 0
        or any(right <= left for left, right in zip(frame_times, frame_times[1:]))
    ):
        raise AlignmentError("Invalid or empty video frame timeline")
    silent_frames = [
        index
        for index, point in enumerate(frame_times)
        if any(word.start <= point < word.end for word in predicted)
        and not any(word.start <= point < word.end for word in reference)
    ]
    accuracy_pass = Fraction(within, len(errors)) >= required_fraction
    return {
        "boundary_count": len(errors),
        "tolerance_ms": float(tolerance * 1000),
        "required_fraction": float(required_fraction),
        "within_tolerance": within,
        "within_fraction": within / len(errors),
        "p95_error_ms": float(p95 * 1000),
        "max_error_ms": float(max(errors) * 1000),
        "accuracy_pass": accuracy_pass,
        "stretch_tolerance_ms": float(stretch_tolerance * 1000),
        "stretch_within_tolerance": stretch_within,
        "stretch_within_fraction": stretch_within / len(errors),
        "stretch_accuracy_pass": Fraction(stretch_within, len(errors))
        >= required_fraction,
        "silent_highlight_frames": len(silent_frames),
        "silent_frame_indices": silent_frames,
        "evaluation_fps": None if supplied_pts else str(frame_rate),
        "frame_timing_source": "supplied_pts" if supplied_pts else "constant_rate_grid",
        "pass": accuracy_pass and not silent_frames,
        "words": details,
    }


def ctc_readings(word: str) -> tuple[str, ...]:
    """Supported English readings, never replacements for the original token."""
    text = unicodedata.normalize("NFKC", word).upper().replace("’", "'")
    if not any(character.isdigit() for character in text):
        return (text,)
    number = text.strip(".,!?;:\"'()[]{}“”‘’")
    if not re.fullmatch(r"(?:0|[1-9][0-9]*|[1-9][0-9]{0,2}(?:,[0-9]{3})+)", number):
        raise AlignmentError(f"Token {word!r} has an unsupported numeric form")
    converter = importlib.import_module("num2words").num2words
    try:
        value = int(number.replace(",", ""))
        spellings = [converter(value, lang="en", to="cardinal")]
        if len(number.replace(",", "")) == 4:
            spellings.append(converter(value, lang="en", to="year"))
    except (ValueError, OverflowError, NotImplementedError) as error:
        raise AlignmentError(f"Token {word!r} cannot be verbalized: {error}") from error
    readings = [
        " ".join(re.findall(r"[A-Z]+", spelling.upper())) for spelling in spellings
    ]
    # Both English conjunction conventions are candidates, not timing repairs.
    readings.extend(
        reading.replace(" AND ", " ")
        for reading in list(readings)
        if " AND " in reading
    )
    return tuple(dict.fromkeys(readings))


def ctc_reading_options(words: list[str]) -> list[tuple[str, ...]]:
    """Bound pronunciation search before model inference; never guess on overflow."""
    choices = [ctc_readings(word) for word in words]
    count = math.prod(len(choice) for choice in choices)
    if count > MAX_NUMERIC_READING_PATHS:
        raise AlignmentError(
            f"Numeric reading alternatives ({count}) exceed the supported limit "
            f"({MAX_NUMERIC_READING_PATHS})"
        )
    return list(product(*choices))


def ctc_targets(
    words: list[str],
    vocabulary: dict[str, int],
    *,
    readings: Sequence[str] | None = None,
) -> tuple[list[int], list[int | None]]:
    targets: list[int] = []
    owners: list[int | None] = []
    for index, word in enumerate(words):
        if index:
            targets.append(vocabulary["|"])
            owners.append(None)
        letters = readings[index] if readings is not None else ctc_readings(word)[0]
        letters = re.sub(r"\s+", " ", letters.strip())
        count = 0
        for letter in letters:
            if letter in vocabulary and letter != "|":
                targets.append(vocabulary[letter])
                owners.append(index)
                count += 1
            elif letter == " ":
                targets.append(vocabulary["|"])
                owners.append(None)
            elif unicodedata.category(letter).startswith("P"):
                continue  # Punctuation is not a spoken character; identity is retained.
            else:
                raise AlignmentError(
                    f"Token {word!r} has unsupported CTC character {letter!r}"
                )
        if not count:
            raise AlignmentError(f"Token {word!r} contains no alignable characters")
    return targets, owners


def qwen_intervals(
    words: list[str], timestamp_ids: list[int], quantum_ms: int | float
) -> list[WordAlignment]:
    """Decode raw classes, deliberately bypassing the SDK's timestamp repair."""
    if len(timestamp_ids) != len(words) * 2:
        raise AlignmentError("Qwen changed token count")
    if any(right < left for left, right in zip(timestamp_ids, timestamp_ids[1:])):
        raise AlignmentError("Qwen emitted non-monotonic timestamps; no repair applied")
    quantum = Fraction(str(quantum_ms)) / 1000
    if quantum <= 0:
        raise AlignmentError("Invalid Qwen timestamp quantum")
    results = []
    for index, text in enumerate(words):
        start, end = timestamp_ids[index * 2 : index * 2 + 2]
        if start < 0 or end <= start:
            raise AlignmentError(
                f"Qwen returned zero/invalid duration for token {text!r}"
            )
        results.append(WordAlignment(index, text, start * quantum, end * quantum))
    return results


def _read_alignment_audio(audio: Path) -> tuple[int, bytes]:
    with wave.open(str(audio), "rb") as file:
        rate, channels, width = (
            file.getframerate(),
            file.getnchannels(),
            file.getsampwidth(),
        )
        raw = file.readframes(file.getnframes())
    if (rate, channels, width) != (16000, 1, 2):
        raise AlignmentError("Alignment input must be 16 kHz mono signed-16-bit WAV")
    if not raw or not any(raw):
        raise AlignmentError("No acoustic activity to support the supplied transcript")
    return rate, raw


def pause_overlaps(
    words: list[WordAlignment],
    speech: list[tuple[int, int]],
    samples: int,
    rate: int,
) -> list[dict[str, Any]]:
    """Observe protected VAD-gap interiors without editing any word estimate."""
    if rate <= 0 or samples <= 0:
        raise AlignmentError("Invalid pause diagnostic audio clock")
    previous = 0
    gaps: list[tuple[int, int]] = []
    for start, end in speech:
        if start < previous or end <= start or end > samples:
            raise AlignmentError("Invalid speech-detector interval")
        gaps.append((previous, start))
        previous = end
    gaps.append((previous, samples))
    observed = []
    for left, right in gaps:
        if Fraction(right - left, rate) < Fraction(1, 4):
            continue
        protected_start, protected_end = (
            Fraction(left, rate) + Fraction(2, 25),
            Fraction(right, rate) - Fraction(2, 25),
        )
        for word in words:
            if word.start < protected_end and word.end > protected_start:
                observed.append(
                    {
                        "word_index": word.index,
                        "token": word.text,
                        "protected_start": float(protected_start),
                        "protected_end": float(protected_end),
                    }
                )
    return observed


def pause_diagnostics(audio: Path, words: list[WordAlignment]) -> dict[str, Any]:
    """Provisional supported Silero-VAD observations, not a calibrated gate."""
    result: dict[str, Any] = {
        "status": "provisional",
        "blocking": False,
        "method": "faster-whisper/silero-vad",
        "min_pause_ms": 250,
        "edge_margin_ms": 80,
        "overlaps": [],
    }
    try:
        rate, raw = _read_alignment_audio(audio)
        numpy = importlib.import_module("numpy")
        vad = importlib.import_module("faster_whisper.vad")
        options = vad.VadOptions(min_silence_duration_ms=250, speech_pad_ms=0)
        speech = vad.get_speech_timestamps(
            numpy.frombuffer(raw, dtype="<i2").astype("float32") / 32768,
            options,
            sampling_rate=rate,
        )
        spans = [(item["start"], item["end"]) for item in speech]
        result["sample_rate"] = rate
        result["speech_intervals"] = speech
        result["overlaps"] = pause_overlaps(words, spans, len(raw) // 2, rate)
    except (ImportError, OSError, RuntimeError, AlignmentError) as error:
        result.update(status="unavailable", error=str(error))
    return result


def align_qwen(
    audio: Path, words: list[str], *, offline: bool = False
) -> list[WordAlignment]:
    """Read model predictions directly: no interpolation, rounding or fallback."""
    _, raw = _read_alignment_audio(audio)
    torch = importlib.import_module("torch")
    qwen = importlib.import_module("qwen_asr")
    hub = importlib.import_module("huggingface_hub")
    numpy = importlib.import_module("numpy")
    snapshot = hub.snapshot_download(
        QWEN_MODEL,
        revision=QWEN_REVISION,
        local_files_only=offline,
        allow_patterns=["*.json", "*.safetensors", "*.txt"],
    )
    wrapper = qwen.Qwen3ForcedAligner.from_pretrained(
        snapshot,
        dtype=torch.float32,
        device_map="cpu",
        use_safetensors=True,
    )
    samples = len(raw) // 2
    waveform = numpy.frombuffer(raw, dtype="<i2").astype("float32") / 32768
    normalized, text = wrapper.aligner_processor.encode_timestamp(
        " ".join(words), "English"
    )
    expected = [wrapper.aligner_processor.clean_token(word) for word in words]
    if normalized != expected:
        raise AlignmentError("Qwen normalization changed original token ownership")
    inputs = wrapper.processor(
        text=[text], audio=[waveform], return_tensors="pt", padding=True
    )
    inputs = inputs.to(wrapper.model.device).to(wrapper.model.dtype)
    with torch.inference_mode():
        logits = wrapper.model.thinker(**inputs).logits
        output_ids = logits.argmax(dim=-1)[0]
        ids = output_ids[inputs["input_ids"][0] == wrapper.timestamp_token_id].tolist()
    results = qwen_intervals(words, ids, wrapper.timestamp_segment_time)
    if any(word.end > Fraction(samples, 16000) for word in results):
        raise AlignmentError(
            "Qwen timestamp exceeds the actual audio; no clamping applied"
        )
    return results


def _mfa_token(word: str) -> str:
    normalized = word.casefold().strip('.,!?;:"()[]')
    if not re.fullmatch(r"[a-z]+(?:'[a-z]+)*", normalized):
        raise AlignmentError(
            f"MFA cannot normalize token {word!r} without an explicit pronunciation"
        )
    return normalized


def mfa_intervals(words: list[str], payload: dict[str, Any]) -> list[WordAlignment]:
    """Decode native lattice frames, not post-processed TextGrid timestamps."""
    raw = payload["words"]
    if len(raw) != len(words):
        raise AlignmentError("MFA changed token count")
    quantum = Fraction(str(payload["frame_shift_ms"])) / 1000
    if quantum <= 0:
        raise AlignmentError("Invalid MFA frame clock")
    results = []
    previous_end = 0
    for index, (word, entry) in enumerate(zip(words, raw)):
        if entry["text"] != _mfa_token(word):
            raise AlignmentError(f"MFA changed token identity for {word!r}")
        start, end = entry["start_frame"], entry["end_frame"]
        if (
            type(start) is not int
            or type(end) is not int
            or start < previous_end
            or end <= start
        ):
            raise AlignmentError(
                f"MFA returned invalid native frame interval for {word!r}"
            )
        results.append(
            WordAlignment(
                index, word, start * quantum, end * quantum, entry.get("score")
            )
        )
        previous_end = end
    return results


def phoneme_windows(
    anchors: list[WordAlignment],
    sample_rate: int,
    sample_count: int,
    *,
    neighbors: int = 0,
) -> list[tuple[int, int]]:
    """Overlapping search contexts from acoustic anchors, never reference labels."""
    if sample_rate <= 0 or sample_count <= 0:
        raise AlignmentError("Invalid phoneme context audio")
    duration = Fraction(sample_count, sample_rate)
    windows = []
    previous_end = Fraction(0)
    for index, anchor in enumerate(anchors):
        if not previous_end <= anchor.start < anchor.end <= duration:
            raise AlignmentError("Invalid or overlapping acoustic anchors")
        first, last = (
            max(0, index - neighbors),
            min(len(anchors) - 1, index + neighbors),
        )
        left = max(Fraction(0), anchors[first].start - Fraction(1, 5))
        right = min(duration, anchors[last].end + Fraction(1, 5))
        if first:
            previous = anchors[first - 1]
            left = max(left, (previous.start + previous.end) / 2)
        if last + 1 < len(anchors):
            following = anchors[last + 1]
            right = min(right, (following.start + following.end) / 2)
        windows.append((math.floor(left * sample_rate), math.ceil(right * sample_rate)))
        previous_end = anchor.end
    return windows


def restore_phoneme_windows(
    anchors: list[WordAlignment],
    windows: list[tuple[int, int]],
    payload: dict[str, Any],
    sample_rate: int,
    *,
    neighbors: int = 0,
) -> list[WordAlignment]:
    """Restore native frame positions; reject censoring/conflicts, never repair."""
    utterances = payload["utterances"]
    if len(windows) != len(anchors) or set(utterances) != {
        f"w{i:04d}" for i in range(len(anchors))
    }:
        raise AlignmentError("Missing or extra local phoneme alignments")
    quantum = Fraction(str(payload["frame_shift_ms"])) / 1000
    result: list[WordAlignment] = []
    problems = []
    for index, (anchor, (left, right)) in enumerate(zip(anchors, windows)):
        first, last = (
            max(0, index - neighbors),
            min(len(anchors) - 1, index + neighbors),
        )
        relative = mfa_intervals(
            [item.text for item in anchors[first : last + 1]],
            {
                "frame_shift_ms": payload["frame_shift_ms"],
                "words": utterances[f"w{index:04d}"],
            },
        )[index - first]
        offset = Fraction(left, sample_rate)
        start, end = offset + relative.start, offset + relative.end
        if (
            relative.start < quantum
            or relative.end > Fraction(right - left, sample_rate) - quantum
        ):
            problems.append(f"{anchor.text!r}: context-edge censored")
        if start > anchor.start + Fraction(1, 50) or end < anchor.end - Fraction(1, 50):
            problems.append(f"{anchor.text!r}: conflicts with acoustic anchor")
        if result and start < result[-1].end:
            problems.append(f"{anchor.text!r}: overlaps previous token")
        result.append(
            WordAlignment(
                anchor.index, anchor.text, start, end, relative.acoustic_score
            )
        )
    if problems:
        raise AlignmentError(
            "Local phoneme alignment rejected: " + "; ".join(problems),
            diagnostics={
                "accepted": False,
                "sample_rate": sample_rate,
                "search_windows_samples": windows,
                "native_frames": payload,
                "raw_target_intervals": [
                    {
                        "index": item.index,
                        "text": item.text,
                        "start_rational_seconds": str(item.start),
                        "end_rational_seconds": str(item.end),
                        "acoustic_score_uncalibrated": item.acoustic_score,
                    }
                    for item in result
                ],
            },
        )
    return result


def _mfa_paths(offline: bool) -> tuple[Path, Path]:
    project = Path(__file__).resolve().parents[3]
    tools = project / "scripts" / "alignment-mfa"
    if not (tools / "pixi.lock").is_file():
        raise RuntimeError(
            "MFA benchmark requires a repository checkout and its Pixi lockfile"
        )
    hub = importlib.import_module("huggingface_hub")
    model = Path(
        hub.snapshot_download(
            MFA_MODEL,
            revision=MFA_REVISION,
            local_files_only=offline,
            allow_patterns=["acoustic/*", "dictionary/*", "g2p/*"],
        )
    )
    return tools, model


def mfa_pronunciation_dictionary(
    base: Path, overrides: Path, metadata: Path, output: Path
) -> None:
    """Derive an explicit lexicon; never alter source/model files or token units."""
    if output.resolve() in (base.resolve(), overrides.resolve()):
        raise ValueError("Pronunciation output must not overwrite an input")
    inventory = set(json.loads(metadata.read_text())["phones"])
    entries: dict[str, list[str]] = {}
    for number, line in enumerate(overrides.read_text().splitlines(), 1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        fields = line.split()
        key = fields[0].casefold()
        if len(fields) < 2 or not re.fullmatch(r"[a-z]+(?:'[a-z]+)*", key):
            raise ValueError(f"Invalid single-token pronunciation on line {number}")
        if key in entries:
            raise ValueError(f"Duplicate pronunciation override for {key!r}")
        unknown = set(fields[1:]) - inventory
        if unknown:
            raise ValueError(
                f"Unknown model phonemes on line {number}: {sorted(unknown)}"
            )
        entries[key] = fields[1:]
    retained = [
        line
        for line in base.read_text().splitlines()
        if not line.strip() or line.split()[0].casefold() not in entries
    ]
    retained.extend(f"{key}\t{' '.join(phones)}" for key, phones in entries.items())
    output.write_text("\n".join(retained) + "\n")


def _run_mfa(
    corpus: Path,
    working: Path,
    tools: Path,
    model: Path,
    *,
    batch: bool = False,
    pronunciations: Path | None = None,
    speaker_adaptation: bool = False,
) -> dict[str, Any]:
    dictionary = model / "dictionary" / "english_us_arpa.dict"
    if pronunciations is not None:
        derived = working / "pronunciations.dict"
        mfa_pronunciation_dictionary(
            dictionary, pronunciations, model / "acoustic" / "meta.json", derived
        )
        dictionary = derived
    state = working / "state"
    frames = working / "frames.json"
    env = {
        **os.environ,
        "MFA_ROOT_DIR": str(state),
        "HF_HUB_OFFLINE": "1",
        "OMP_NUM_THREADS": "1",
    }
    runner = [
        "pixi",
        "run",
        "--manifest-path",
        str(tools / "pixi.toml"),
        "--locked",
    ]
    commands = [
        runner
        + [
            "mfa",
            "align",
            str(corpus),
            str(dictionary),
            str(model / "acoustic"),
            str(working / "export"),
            "--output_format",
            "json",
            "--no_textgrid_cleanup",
            "--no_tokenization",
            "--num_jobs",
            "1",
            "--no_use_mp",
            "--g2p_model_path",
            str(model / "g2p" / "english_us_arpa"),
        ],
        runner
        + [
            "python",
            str(tools / "extract-frames.py"),
            str(state / "corpus"),
            str(model / "acoustic" / "meta.json"),
            str(frames),
        ],
    ]
    if speaker_adaptation:
        commands[-1].append("--adapted")
    else:
        commands[0].append("--single_speaker")
    if batch:
        commands[-1].append("--batch")
    for command in commands:
        process = subprocess.run(
            command, env=env, capture_output=True, text=True, timeout=180
        )
        if process.returncode:
            # A runtime/search failure does not prove a word was unspoken.
            raise RuntimeError(
                f"MFA benchmark command failed: {process.stderr[-2000:]} "
                f"{process.stdout[-1000:]}"
            )
    payload = json.loads(frames.read_text())
    if not isinstance(payload, dict):
        raise RuntimeError("Invalid native MFA frame payload")
    return payload


def align_mfa(
    audio: Path,
    words: list[str],
    *,
    offline: bool = False,
    pronunciations: Path | None = None,
    speaker_adaptation: bool = False,
) -> list[WordAlignment]:
    """Run the repository's locked, supported MFA alignment pipeline."""
    rate, raw = _read_alignment_audio(audio)
    transcript = " ".join(_mfa_token(word) for word in words)
    tools, model = _mfa_paths(offline)
    with tempfile.TemporaryDirectory(prefix="eve-mfa-") as temporary:
        working = Path(temporary)
        corpus = working / "corpus"
        corpus.mkdir()
        shutil.copyfile(audio, corpus / "speech.wav")
        (corpus / "speech.lab").write_text(transcript + "\n")
        results = mfa_intervals(
            words,
            _run_mfa(
                corpus,
                working,
                tools,
                model,
                pronunciations=pronunciations,
                speaker_adaptation=speaker_adaptation,
            ),
        )
    if any(word.end > Fraction(len(raw) // 2, rate) for word in results):
        raise AlignmentError("MFA timestamp exceeds actual audio; no clamping applied")
    return results


def align_ctc_phoneme(
    audio: Path,
    words: list[str],
    *,
    offline: bool = False,
    pronunciations: Path | None = None,
) -> list[WordAlignment]:
    """Native phoneme alignment in overlapping CTC-derived search contexts."""
    rate, raw = _read_alignment_audio(audio)
    anchors = align_ctc(audio, words, offline=offline)
    windows = phoneme_windows(anchors, rate, len(raw) // 2, neighbors=1)
    tools, model = _mfa_paths(offline)
    with tempfile.TemporaryDirectory(prefix="eve-mfa-local-") as temporary:
        working = Path(temporary)
        corpus = working / "corpus"
        corpus.mkdir()
        for index, (text, (left, right)) in enumerate(zip(words, windows)):
            name = f"w{index:04d}"
            with wave.open(str(corpus / f"{name}.wav"), "wb") as clip:
                clip.setparams((1, 2, rate, 0, "NONE", "not compressed"))
                clip.writeframes(raw[left * 2 : right * 2])
            context = words[max(0, index - 1) : index + 2]
            (corpus / f"{name}.lab").write_text(
                " ".join(_mfa_token(item) for item in context) + "\n"
            )
        payload = _run_mfa(
            corpus, working, tools, model, batch=True, pronunciations=pronunciations
        )
        return restore_phoneme_windows(anchors, windows, payload, rate, neighbors=1)


def refine_quiet_edges(
    words: list[WordAlignment], samples: Sequence[float], sample_rate: int
) -> list[WordAlignment]:
    """Use well-separated signal edges only; RMS cannot split connected words.

    Five-millisecond RMS windows use a conservative amplitude floor: one PCM16
    step or 0.001 times the 95th-percentile RMS, whichever is larger. An edge
    needs at least 80 ms of adjacent quiet. Search limits (80 ms for starts,
    200 ms for ends) bound eligibility; they are never added to timestamps.
    Uninformative audio and unsupported interior boundaries stay unchanged.
    """
    if sample_rate <= 0:
        raise AlignmentError("Invalid acoustic sample rate")
    window = max(1, sample_rate // 200)
    windows = [
        samples[start : start + window] for start in range(0, len(samples), window)
    ]
    if not windows:
        return list(words)
    energy = [
        math.sqrt(sum(value * value for value in block) / len(block))
        for block in windows
    ]
    ordered = sorted(energy)
    floor = max(1 / 32768, ordered[math.ceil(len(ordered) * 0.95) - 1] * 0.001)
    quiet_samples = math.ceil(sample_rate * Fraction(2, 25))
    regions: list[tuple[int, int]] = []
    for index, (block, rms) in enumerate(zip(windows, energy)):
        if rms <= floor:
            continue
        audible = [offset for offset, value in enumerate(block) if abs(value) > floor]
        begin_sample = index * window + audible[0]
        end_sample = index * window + audible[-1] + 1
        if regions and begin_sample - regions[-1][1] < quiet_samples:
            regions[-1] = (regions[-1][0], end_sample)
        else:
            regions.append((begin_sample, end_sample))
    refined: list[WordAlignment] = []
    for index, word in enumerate(words):
        start, end = word.start, word.end
        overlaps = [
            r
            for r, region in enumerate(regions)
            if Fraction(region[0], sample_rate) < end
            and Fraction(region[1], sample_rate) > start
        ]
        if overlaps:
            first, last = overlaps[0], overlaps[-1]
            a, b = regions[first][0], regions[last][1]
            leading = a - (regions[first - 1][1] if first else 0)
            trailing = (
                regions[last + 1][0] if last + 1 < len(regions) else len(samples)
            ) - b
            onset, offset = Fraction(a, sample_rate), Fraction(b, sample_rate)
            previous_end = refined[-1].end if refined else Fraction(0)
            next_start = (
                words[index + 1].start
                if index + 1 < len(words)
                else Fraction(len(samples), sample_rate)
            )
            if (
                leading >= quiet_samples
                and abs(onset - start) <= Fraction(2, 25)
                and onset >= previous_end
            ):
                start = onset
            if (
                trailing >= quiet_samples
                and abs(offset - end) <= Fraction(1, 5)
                and offset <= next_start
            ):
                end = offset
        if end <= start:
            raise AlignmentError("Acoustic refinement produced an invalid interval")
        refined.append(
            WordAlignment(
                word.index,
                word.text,
                start,
                end,
                word.acoustic_score,
                word.alignment_text,
            )
        )
    return refined


def _ctc_emissions(
    model: Any,
    processor: Any,
    waveform: Any,
    rate: int,
    *,
    metadata: dict[str, Any] | None = None,
) -> Any:
    """Infer bounded context, retaining each original convolution-grid frame once."""
    if metadata is not None:
        metadata.clear()
    if rate <= 0:
        raise AlignmentError("Invalid acoustic sample rate")
    torch = importlib.import_module("torch")
    samples = len(waveform)
    hop = math.prod(model.config.conv_stride)
    windows: list[dict[str, int]] = []
    if samples <= rate * CTC_WINDOW_SECONDS:
        values = model(
            **processor(waveform, sampling_rate=rate, return_tensors="pt")
        ).logits
        total_frames = values.shape[1]
        strategy = "single-window"
        windows.append(
            {
                "input_start_sample": 0,
                "input_end_sample": samples,
                "kept_frame_start": 0,
                "kept_frame_end": total_frames,
            }
        )
        receptive: int | None = None
    else:
        receptive, step = 1, 1
        for kernel, stride in zip(model.config.conv_kernel, model.config.conv_stride):
            receptive += (kernel - 1) * step
            step *= stride
        context = rate * CTC_CONTEXT_SECONDS // hop
        window_frames = rate * CTC_WINDOW_SECONDS // hop
        core_frames = window_frames - 2 * context
        if core_frames <= 0:
            raise AlignmentError("Unsupported native CTC window geometry")
        total_frames = (samples - receptive) // hop + 1
        pieces = []
        core_start = 0
        while core_start < total_frames:
            core_end = min(
                total_frames,
                core_start
                + (window_frames - context if core_start == 0 else core_frames),
            )
            left, right = (
                max(0, core_start - context),
                min(total_frames, core_end + context),
            )
            first_sample = left * hop
            last_sample = (
                samples if right == total_frames else (right - 1) * hop + receptive
            )
            chunk = waveform[first_sample:last_sample]
            emitted = model(
                **processor(chunk, sampling_rate=rate, return_tensors="pt")
            ).logits
            if emitted.shape[1] != right - left:
                raise AlignmentError(
                    "CTC window emitted an unexpected native frame count; "
                    "no resampling applied"
                )
            pieces.append(emitted[:, core_start - left : core_end - left])
            windows.append(
                {
                    "input_start_sample": first_sample,
                    "input_end_sample": last_sample,
                    "kept_frame_start": core_start,
                    "kept_frame_end": core_end,
                }
            )
            core_start = core_end
        values = torch.cat(pieces, dim=1)
        if values.shape[1] != total_frames:
            raise AlignmentError("CTC windows lost or duplicated native frames")
        strategy = "overlap-windowed"
    if metadata is not None:
        metadata.update(
            strategy=strategy,
            sample_rate=rate,
            input_samples=samples,
            native_frame_count=total_frames,
            hop_samples=hop,
            nominal_window_seconds=CTC_WINDOW_SECONDS,
            context_seconds=CTC_CONTEXT_SECONDS,
            window_count=len(windows),
            frame_ownership="exact_native_grid",
            windows=windows,
        )
        if receptive is not None:
            metadata["receptive_field_samples"] = receptive
    return values


def align_ctc(
    audio: Path,
    words: list[str],
    *,
    offline: bool = False,
    inference_metadata: dict[str, Any] | None = None,
) -> list[WordAlignment]:
    """Return native character-span estimates from bounded acoustic context."""
    return _align_ctc(
        audio,
        words,
        offline=offline,
        word_boundaries=False,
        inference_metadata=inference_metadata,
    )


def align_ctc_boundary(
    audio: Path,
    words: list[str],
    *,
    offline: bool = False,
    inference_metadata: dict[str, Any] | None = None,
) -> list[WordAlignment]:
    """Experimental delimiter evidence plus conservative signal-edge refinement."""
    return _align_ctc(
        audio,
        words,
        offline=offline,
        word_boundaries=True,
        inference_metadata=inference_metadata,
    )


def _align_ctc(
    audio: Path,
    words: list[str],
    *,
    offline: bool,
    word_boundaries: bool,
    inference_metadata: dict[str, Any] | None = None,
) -> list[WordAlignment]:
    rate, raw = _read_alignment_audio(audio)
    reading_options = ctc_reading_options(words)
    torch = importlib.import_module("torch")
    torchaudio = importlib.import_module("torchaudio")
    transformers = importlib.import_module("transformers")
    numpy = importlib.import_module("numpy")
    options = {"revision": CTC_REVISION, "local_files_only": offline}
    processor = transformers.AutoProcessor.from_pretrained(CTC_MODEL, **options)
    model = transformers.AutoModelForCTC.from_pretrained(
        CTC_MODEL, use_safetensors=True, **options
    ).eval()
    vocabulary = processor.tokenizer.get_vocab()
    waveform = numpy.frombuffer(raw, dtype="<i2").astype("float32") / 32768
    with torch.inference_mode():
        emissions = (
            _ctc_emissions(
                model, processor, waveform, rate, metadata=inference_metadata
            )
            .log_softmax(dim=-1)
            .to(torch.float32)
        )
        best: tuple[Any, list[int | None], tuple[str, ...]] | None = None
        best_score = -math.inf
        tied = False
        for readings in reading_options:
            targets, owners = ctc_targets(words, vocabulary, readings=readings)
            if word_boundaries:
                targets.append(vocabulary["|"])
                owners.append(None)
            required = len(targets) + sum(a == b for a, b in zip(targets, targets[1:]))
            if required > emissions.shape[1]:
                continue
            paths, scores = torchaudio.functional.forced_align(
                emissions,
                torch.tensor([targets], dtype=torch.int64),
                blank=processor.tokenizer.pad_token_id,
            )
            score = float(scores.sum(dtype=torch.float64).item())
            if not math.isfinite(score):
                continue
            spans = torchaudio.functional.merge_tokens(
                paths[0], scores[0], blank=processor.tokenizer.pad_token_id
            )
            if [span.token for span in spans] != targets:
                raise AlignmentError(
                    "CTC alignment omitted or changed transcript tokens"
                )
            if score > best_score:
                best, best_score, tied = (spans, owners, readings), score, False
            elif score == best_score:
                tied = True
        if best is None or tied:
            raise AlignmentError(
                "No unambiguous finite native CTC reading; no fallback applied"
            )
        spans, owners, selected_readings = best
    clock = FrameClock(rate, math.prod(model.config.conv_stride))
    results: list[WordAlignment] = []
    for index, text in enumerate(words):
        matched = [span for span, owner in zip(spans, owners) if owner == index]
        last = matched[-1].end
        if word_boundaries:
            final_character = max(k for k, owner in enumerate(owners) if owner == index)
            last = spans[final_character + 1].end
        start, end = clock.interval(matched[0].start, last)
        weight = sum(span.end - span.start for span in matched)
        score = sum(span.score * (span.end - span.start) for span in matched) / weight
        normalized = (
            selected_readings[index]
            if any(c.isdigit() for c in unicodedata.normalize("NFKC", text))
            else None
        )
        results.append(WordAlignment(index, text, start, end, score, normalized))
    return (
        refine_quiet_edges(results, waveform.tolist(), rate)
        if word_boundaries
        else results
    )
