from __future__ import annotations

import json
import wave
from argparse import Namespace
from contextlib import redirect_stderr
from copy import deepcopy
from fractions import Fraction
from io import StringIO
from pathlib import Path
from types import SimpleNamespace

import pytest

from eve_cli import alignment, tjm_v11
from eve_cli.commands import transcribe
from eve_cli.main import build_parser, main


class FakeWord:
    def __init__(self, start: float | None, end: float | None, word: str) -> None:
        self.start = start
        self.end = end
        self.word = word


class FakeSegment:
    def __init__(
        self,
        *,
        start: float,
        end: float,
        text: str,
        words: list[FakeWord],
        speaker: str | None = None,
    ) -> None:
        self.start = start
        self.end = end
        self.text = text
        self.words = words
        self.speaker = speaker


def make_run_args(media: Path, output: Path, **overrides: object) -> Namespace:
    values: dict[str, object] = {
        "inputs": [str(media)],
        "output": str(output),
        "model": "base.en",
        "language": "en",
        "beam_size": 5,
        "device": "auto",
        "backend": "faster-whisper",
        "pretty": False,
        "max_segment_duration": 0.0,
        "max_segment_words": 0,
        "tag_fillers": False,
        "verbatim": False,
        "hotwords": None,
        "vad": False,
        "stub": False,
        "json": False,
        "command": "transcribe",
    }
    values.update(overrides)
    return Namespace(**values)


@pytest.fixture(autouse=True)
def mock_source_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub clocks for empty ASR fixtures; real probing has media-level tests."""
    monkeypatch.setattr(transcribe, "probe_audio_sample_rate", lambda _path: 48000)


@pytest.fixture
def aligned_job(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    media, output = tmp_path / "clip.mp4", tmp_path / "result.tjm.json"
    media.write_bytes(b"source-must-not-change")
    monkeypatch.setattr(
        transcribe,
        "transcribe_faster_whisper",
        lambda args, paths: [
            {
                "id": "clip01-s0001",
                "source": "clip01",
                "start": 0.1,
                "end": 0.9,
                "words": [
                    {"token": "Hello", "start": 0.1, "end": 0.4},
                    {"token": "world.", "start": 0.5, "end": 0.9},
                ],
            }
        ],
    )

    def extract(_source: Path, target: Path) -> None:
        with wave.open(str(target), "wb") as clip:
            clip.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
            clip.writeframes(b"\1\0" * 16000)

    monkeypatch.setattr(transcribe, "ffmpeg_extract", extract)

    def fake_alignment(
        *args: object, **kwargs: object
    ) -> list[alignment.WordAlignment]:
        metadata = kwargs.get("inference_metadata")
        if isinstance(metadata, dict):
            metadata.update(
                strategy="single-window",
                sample_rate=16000,
                native_frame_count=49,
                window_count=1,
                frame_ownership="exact_native_grid",
            )
        return [
            alignment.WordAlignment(
                0, "Hello", Fraction("0.100021"), Fraction("0.400104")
            ),
            alignment.WordAlignment(
                1, "world.", Fraction("0.500021"), Fraction("0.900104")
            ),
        ]

    monkeypatch.setattr(alignment, "align_ctc", fake_alignment)
    monkeypatch.setattr(
        alignment,
        "pause_diagnostics",
        lambda *a, **kw: {"status": "provisional", "blocking": False, "overlaps": []},
        raising=False,
    )
    return media, output


def test_ctc_normal_mode_publishes_native_ticks_despite_experimental_status(
    aligned_job: tuple[Path, Path],
) -> None:
    media, output = aligned_job
    report = output.with_name("decision.json")
    assert (
        main(
            [
                "transcribe",
                str(media),
                "--output",
                str(output),
                "--word-aligner",
                "ctc",
                "--alignment-report",
                str(report),
            ]
        )
        == 0
    )
    data = json.loads(output.read_text())
    tjm_v11.validate_manifest(data)
    words = [w for segment in data["segments"] for w in segment["words"]]
    assert [(w["spoken"], w["start_tick"], w["end_tick"]) for w in words] == [
        ("Hello", 4801, 19205),
        ("world.", 24001, 43205),
    ]
    audit = data["alignment"]
    assert audit["engine"] == "ctc" and audit["forced"] is False
    assert audit["qualification"] == "experimental"
    assert audit["quality_concerns"] == []
    assert audit["sources"][0]["inference"] == {
        "strategy": "single-window",
        "sample_rate": 16000,
        "native_frame_count": 49,
        "window_count": 1,
        "frame_ownership": "exact_native_grid",
    }
    decision = json.loads(report.read_text())
    assert decision["publication_permitted"] is True
    assert decision["alignment"] == audit
    assert media.read_bytes() == b"source-must-not-change"


def test_numeric_alignment_publishes_original_tokens_and_reading_provenance(
    aligned_job: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    media, output = aligned_job
    monkeypatch.setattr(
        transcribe,
        "transcribe_faster_whisper",
        lambda args, paths: [
            {
                "id": "a",
                "source": "clip01",
                "start": 0.1,
                "end": 0.9,
                "words": [
                    {"token": "11,", "start": 0.1, "end": 0.4},
                    {"token": "2027.", "start": 0.5, "end": 0.9},
                ],
            }
        ],
    )
    monkeypatch.setattr(
        alignment,
        "align_ctc",
        lambda *a, **kw: [
            alignment.WordAlignment(
                0, "11,", Fraction("0.1"), Fraction("0.4"), alignment_text="ELEVEN"
            ),
            alignment.WordAlignment(
                1,
                "2027.",
                Fraction("0.5"),
                Fraction("0.9"),
                alignment_text="TWENTY TWENTY SEVEN",
            ),
        ],
    )
    assert (
        main(
            ["transcribe", str(media), "--output", str(output), "--word-aligner", "ctc"]
        )
        == 0
    )
    data = json.loads(output.read_text())
    assert [w["spoken"] for s in data["segments"] for w in s["words"]] == [
        "11,",
        "2027.",
    ]
    normalization = data["alignment"]["sources"][0]["normalization"]
    assert (
        normalization["library"] == "num2words" and normalization["version"] == "0.5.14"
    )
    assert normalization["readings"] == [
        {"word_index": 0, "text": "11,", "alignment_text": "ELEVEN"},
        {"word_index": 1, "text": "2027.", "alignment_text": "TWENTY TWENTY SEVEN"},
    ]
    assert data["alignment"]["forced"] is False


def test_word_alignment_does_not_shrink_existing_selected_media(
    aligned_job: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    media, output = aligned_job
    monkeypatch.setattr(
        alignment,
        "align_ctc",
        lambda *a, **kw: [
            alignment.WordAlignment(0, "Hello", Fraction("0.2"), Fraction("0.3")),
            alignment.WordAlignment(1, "world.", Fraction("0.6"), Fraction("0.8")),
        ],
    )
    assert (
        main(
            ["transcribe", str(media), "--output", str(output), "--word-aligner", "ctc"]
        )
        == 0
    )
    segment = json.loads(output.read_text())["segments"][0]
    assert (segment["start_tick"], segment["end_tick"]) == (4800, 43200)
    assert segment["words"][0]["start_tick"] == 9600


@pytest.mark.parametrize(
    "delta,force",
    [("0.16", False), ("0.161", False), ("0.161", True)],
)
def test_ctc_disagreement_is_nonblocking_and_never_changes_estimates(
    aligned_job: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    delta: str,
    force: bool,
) -> None:
    media, output = aligned_job
    output.write_bytes(b"previous manifest")
    report = output.with_name("decision.json")
    start = Fraction("0.1") + Fraction(delta)
    monkeypatch.setattr(
        alignment,
        "align_ctc",
        lambda *a, **kw: [
            alignment.WordAlignment(0, "Hello", start, Fraction("0.4")),
            alignment.WordAlignment(1, "world.", Fraction("0.5"), Fraction("0.9")),
        ],
    )
    result = main(
        [
            "transcribe",
            str(media),
            "--output",
            str(output),
            "--word-aligner",
            "ctc",
            "--alignment-report",
            str(report),
        ]
        + (["--force"] if force else [])
    )
    assert result == 0
    decision = json.loads(report.read_text())
    assert decision["publication_permitted"] is True
    audit = decision["alignment"]
    assert audit["forced"] is False  # Even --force is not an override without concerns.
    assert audit["quality_concerns"] == []
    assert bool(audit["diagnostics"]) is (delta == "0.161")
    if delta == "0.161":
        assert audit["diagnostics"][0]["code"] == "boundary_disagreement"
        assert audit["diagnostics"][0]["boundary"] == "start"
        assert audit["diagnostics"][0]["blocking"] is False
    data = json.loads(output.read_text())
    assert data["alignment"] == audit
    assert data["segments"][0]["words"][0]["start_tick"] == round(start * 48000)


@pytest.mark.parametrize("force", [False, True])
@pytest.mark.parametrize(
    "recognized_start,recognized_end",
    [(None, None), (0.1, 0.1), (float("nan"), 0.4), (0.1, float("inf"))],
    ids=["missing", "zero", "nonfinite-start", "nonfinite-end"],
)
def test_unavailable_comparison_timing_publishes_native_estimates_without_override(
    aligned_job: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    force: bool,
    recognized_start: float | None,
    recognized_end: float | None,
) -> None:
    media, output = aligned_job
    output.write_bytes(b"previous manifest")
    report = output.with_name("decision.json")
    monkeypatch.setattr(
        transcribe,
        "transcribe_faster_whisper",
        lambda args, paths: [
            {
                "id": "a",
                "source": "clip01",
                "start": 0.1,
                "end": 0.9,
                "words": [
                    {
                        "token": "Hello",
                        "start": recognized_start,
                        "end": recognized_end,
                    },
                    {"token": "world.", "start": 0.5, "end": 0.9},
                ],
            }
        ],
    )
    assert (
        main(
            [
                "transcribe",
                str(media),
                "--output",
                str(output),
                "--word-aligner",
                "ctc",
                "--alignment-report",
                str(report),
            ]
            + (["--force"] if force else [])
        )
        == 0
    )
    decision = json.loads(report.read_text())
    audit = decision["alignment"]
    assert decision["publication_permitted"] is True
    assert audit["forced"] is False
    assert audit["qualification"] == "experimental"
    assert audit["quality_concerns"] == []
    (diagnostic,) = audit["diagnostics"]
    assert diagnostic["code"] == "recognizer_timing_unavailable"
    assert diagnostic["blocking"] is False
    assert diagnostic["source"] == "clip01" and diagnostic["word_index"] == 0
    assert diagnostic["recognizer_start_seconds"] == (
        None if recognized_start is None else str(recognized_start)
    )
    assert diagnostic["recognizer_end_seconds"] == (
        None if recognized_end is None else str(recognized_end)
    )
    # Non-finite comparison values must not introduce non-standard JSON numbers.
    for artifact in (report, output):
        json.loads(
            artifact.read_text(), parse_constant=lambda value: pytest.fail(value)
        )
    data = json.loads(output.read_text())
    tjm_v11.validate_manifest(data)
    assert data["alignment"] == audit
    assert [
        (word["spoken"], word["start_tick"], word["end_tick"])
        for word in data["segments"][0]["words"]
    ] == [
        ("Hello", 4801, 19205),
        ("world.", 24001, 43205),
    ]
    assert media.read_bytes() == b"source-must-not-change"


@pytest.mark.parametrize(
    "bad",
    [
        "zero",
        "reversed",
        "negative",
        "outside",
        "overlap",
        "missing",
        "nonfinite",
        "subtick",
        "count",
        "identity",
        "failed-model",
        "unavailable-model",
    ],
)
def test_force_never_bypasses_aligned_timing_integrity(
    aligned_job: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    bad: str,
) -> None:
    media, output = aligned_job
    output.write_bytes(b"previous manifest")
    pairs = {
        "zero": (0.1, 0.1),
        "reversed": (0.2, 0.1),
        "negative": (-0.1, 0.2),
        "outside": (0.1, 1.1),
        "overlap": (0.1, 0.6),
        "missing": (None, 0.4),
        "nonfinite": (float("nan"), 0.4),
        "subtick": (0.1, 0.100001),
    }
    start, end = pairs.get(bad, (0.1, 0.4))
    predicted = [
        SimpleNamespace(
            index=0, text="Oops" if bad == "identity" else "Hello", start=start, end=end
        ),
        SimpleNamespace(index=1, text="world.", start=0.5, end=0.9),
    ]
    if bad == "count":
        predicted.pop()

    def align(*args: object, **kwargs: object) -> list[SimpleNamespace]:
        if bad == "failed-model":
            raise alignment.AlignmentError("native inference failed")
        if bad == "unavailable-model":
            raise OSError("native model unavailable")
        return predicted

    monkeypatch.setattr(alignment, "align_ctc", align)
    assert (
        main(
            [
                "transcribe",
                str(media),
                "--output",
                str(output),
                "--word-aligner",
                "ctc",
                "--force",
            ]
        )
        == 1
    )
    assert output.read_bytes() == b"previous manifest"
    assert media.read_bytes() == b"source-must-not-change"


@pytest.mark.parametrize("force", [False, True])
def test_joint_caption_boundaries_publish_without_duplicate_media(
    aligned_job: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    force: bool,
) -> None:
    media, output = aligned_job
    output.write_bytes(b"previous manifest")
    monkeypatch.setattr(
        transcribe,
        "transcribe_faster_whisper",
        lambda args, paths: [
            {
                "id": "a",
                "source": "clip01",
                "start": 0.1,
                "end": 0.5,
                "words": [{"token": "Hello", "start": 0.1, "end": 0.5}],
            },
            {
                "id": "b",
                "source": "clip01",
                "start": 0.5,
                "end": 0.9,
                "words": [{"token": "world.", "start": 0.5, "end": 0.9}],
            },
        ],
    )
    monkeypatch.setattr(
        alignment,
        "align_ctc",
        lambda *a, **kw: [
            alignment.WordAlignment(0, "Hello", Fraction("0.1"), Fraction("0.51")),
            alignment.WordAlignment(1, "world.", Fraction("0.51"), Fraction("0.9")),
        ],
    )
    assert (
        main(
            [
                "transcribe",
                str(media),
                "--output",
                str(output),
                "--word-aligner",
                "ctc",
                "--max-segment-words",
                "0",
            ]
            + (["--force"] if force else [])
        )
        == 0
    )
    data = json.loads(output.read_text())
    tjm_v11.validate_manifest(data)
    first, second = data["segments"]
    assert [(s["id"], s["start_tick"], s["end_tick"]) for s in data["segments"]] == [
        ("a", 4800, 24480),
        ("b", 24480, 43200),
    ]
    assert first["words"][0]["end_tick"] == second["words"][0]["start_tick"] == 24480
    assert data["alignment"]["forced"] is False
    assert media.read_bytes() == b"source-must-not-change"


@pytest.mark.parametrize(
    "envelopes,words",
    [
        ([("0.1", "0.55"), ("0.45", "0.9")], [("0.1", "0.5"), ("0.6", "0.8")]),
        (
            [("0", "0.5"), ("0.3", "0.8"), ("0.6", "1")],
            [("0.1", "0.2"), ("0.4", "0.5"), ("0.7", "0.8")],
        ),
        (
            [("0.1", "0.3"), ("0.4", "0.7"), ("0.2", "0.95")],
            [("0.12", "0.2"), ("0.5", "0.6"), ("0.8", "0.9")],
        ),
        (
            [("0", "0.2"), ("0.4", "0.7"), ("0.6", "0.9")],
            [("0.05", "0.15"), ("0.45", "0.55"), ("0.75", "0.85")],
        ),
        ([("0.1", "0.5"), ("0.5", "0.9")], [("0.2", "0.3"), ("0.6", "0.8")]),
    ],
)
def test_caption_partition_preserves_coverage_words_and_grouping(
    envelopes: list[tuple[str, str]],
    words: list[tuple[str, str]],
) -> None:
    segments = [
        {
            "id": str(i),
            "start": Fraction(left),
            "end": Fraction(right),
            "words": [
                {
                    "token": str(i),
                    "start": Fraction(words[i][0]),
                    "end": Fraction(words[i][1]),
                }
            ],
            "notes": "keep me",
        }
        for i, (left, right) in enumerate(envelopes)
    ]
    before = deepcopy(segments)
    transcribe._partition_caption_envelopes(segments)
    for old, new in zip(before, segments):
        assert {k: v for k, v in old.items() if k not in ("start", "end")} == {
            k: v for k, v in new.items() if k not in ("start", "end")
        }
        assert new["start"] <= new["words"][0]["start"]
        assert new["end"] >= new["words"][-1]["end"]
    assert all(a["end"] <= b["start"] for a, b in zip(segments, segments[1:]))
    # Every original source sample is retained exactly once, including nested
    # overlaps whose outer envelope reaches beyond its neighbors.
    for tick in range(1000):
        expected = any(
            round(s["start"] * 1000) <= tick < round(s["end"] * 1000) for s in before
        )
        count = sum(
            round(s["start"] * 1000) <= tick < round(s["end"] * 1000) for s in segments
        )
        assert count == int(expected)
    for point in [Fraction(i, 30) for i in range(30)] + [
        Fraction("0.375"),
        Fraction("0.6001"),
    ]:
        assert sum(s["start"] <= point < s["end"] for s in segments) == int(
            any(s["start"] <= point < s["end"] for s in before)
        )
    # Captions that only touch are already valid and must not be moved.
    if envelopes == [("0.1", "0.5"), ("0.5", "0.9")]:
        assert segments == before


def test_force_and_report_cannot_bypass_configuration_or_overwrite_inputs(
    aligned_job: tuple[Path, Path],
) -> None:
    media, output = aligned_job
    assert main(["transcribe", str(media), "--output", str(output), "--force"]) == 1
    assert (
        main(
            [
                "transcribe",
                str(media),
                "--output",
                str(output),
                "--word-aligner",
                "ctc",
                "--alignment-report",
                str(media),
                "--force",
            ]
        )
        == 1
    )
    assert not output.exists()
    assert media.read_bytes() == b"source-must-not-change"


@pytest.mark.parametrize("rate", [44100, 48000])
def test_transcribe_emits_valid_v11_without_millisecond_rounding(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    rate: int,
) -> None:
    media = tmp_path / "clip.mp4"
    media.touch()
    output = tmp_path / "manifest.json"
    monkeypatch.setattr(transcribe, "probe_audio_sample_rate", lambda _path: rate)
    raw = FakeSegment(
        start=1.000021,
        end=1.000104,
        text="tick fine.",
        words=[
            FakeWord(1.000021, 1.000042, "tick"),
            FakeWord(1.000042, 1.000104, "fine."),
        ],
    )
    monkeypatch.setattr(
        transcribe,
        "transcribe_faster_whisper",
        lambda args, paths: transcribe.segment_to_dict("clip01", 1, raw, 0),
    )
    assert (
        main(
            [
                "transcribe",
                str(media),
                "--output",
                str(output),
                "--max-segment-words",
                "2",
            ]
        )
        == 0
    )
    manifest = json.loads(output.read_text())
    tjm_v11.validate_manifest(manifest)
    assert manifest["version"] == "1.1"
    assert manifest["sources"][0]["timebase"] == {"numerator": 1, "denominator": rate}
    assert manifest["render"] == {"filler_policy": "keep", "preserve_short_gaps": 0}
    segment = manifest["segments"][0]
    assert (segment["start_tick"], segment["end_tick"]) == (rate + 1, rate + 5)
    assert segment["spoken_text"] == segment["display_text"] == "tick fine."
    assert [(word["start_tick"], word["end_tick"]) for word in segment["words"]] == [
        (rate + 1, rate + 2),
        (rate + 2, rate + 5),
    ]
    assert [word["spoken"] for word in segment["words"]] == ["tick", "fine."]
    assert segment["words"][0]["start"] == (rate + 1) / rate


def test_each_transcribed_source_uses_its_own_sample_grid(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    files = [tmp_path / "44.mp4", tmp_path / "48.mp4"]
    for file in files:
        file.touch()
    monkeypatch.setattr(
        transcribe,
        "probe_audio_sample_rate",
        lambda path: 44100 if path.name == "44.mp4" else 48000,
    )
    monkeypatch.setattr(
        transcribe,
        "transcribe_faster_whisper",
        lambda args, paths: [
            {
                "id": f"clip{index:02d}-s0001",
                "source": f"clip{index:02d}",
                "start": 0.25,
                "end": 0.5,
                "words": [{"token": "hello", "start": 0.25, "end": 0.5}],
            }
            for index in range(1, 3)
        ],
    )
    output = tmp_path / "multi.tjm.json"
    assert (
        main(["transcribe", *(str(file) for file in files), "--output", str(output)])
        == 0
    )
    manifest = json.loads(output.read_text())
    tjm_v11.validate_manifest(manifest)
    assert [source["timebase"]["denominator"] for source in manifest["sources"]] == [
        44100,
        48000,
    ]
    assert [segment["end_tick"] for segment in manifest["segments"]] == [22050, 24000]


@pytest.mark.parametrize(
    "start,end", [(1.0, 1.0), (1.1, 1.0), (None, 1.0), (1.000001, 1.000002)]
)
def test_transcribe_never_fabricates_a_positive_word_interval(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    start: float | None,
    end: float,
) -> None:
    media = tmp_path / "clip.mp4"
    media.touch()
    output = tmp_path / "manifest.json"
    output.write_text("previous valid output")
    monkeypatch.setattr(
        transcribe,
        "transcribe_faster_whisper",
        lambda args, paths: [
            {
                "id": "bad-word",
                "source": "clip01",
                "start": 0,
                "end": 2,
                "text": "it.",
                "words": [{"token": "it.", "start": start, "end": end}],
            }
        ],
    )
    assert (
        main(
            [
                "transcribe",
                str(media),
                "--output",
                str(output),
                "--max-segment-words",
                "0",
            ]
        )
        == 1
    )
    assert output.read_text() == "previous valid output"
    error = capsys.readouterr().err
    assert "it." in error
    assert "bad-word" in error
    assert "Traceback" not in error


def test_transcribe_parser_matches_legacy_defaults() -> None:
    parser = build_parser()
    args = parser.parse_args(["transcribe", "input.mp4", "--output", "manifest.json"])

    assert args.command == "transcribe"
    assert args.inputs == ["input.mp4"]
    assert args.output == "manifest.json"
    assert args.model == "medium.en"
    assert args.language == "en"
    assert args.beam_size == 5
    assert args.device == "auto"
    assert args.backend == "faster-whisper"
    assert args.max_segment_duration == 0.0
    assert args.max_segment_words == 8
    assert args.tag_fillers is False
    assert args.verbatim is True
    assert args.hotwords is None
    assert args.pretty is False
    assert args.stub is False


def test_transcribe_parser_accepts_tag_fillers_and_verbatim_flags() -> None:
    parser = build_parser()
    args = parser.parse_args(
        [
            "transcribe",
            "input.mp4",
            "--output",
            "manifest.json",
            "--tag-fillers",
            "--verbatim",
        ]
    )

    assert args.tag_fillers is True
    assert args.verbatim is True


def test_transcribe_missing_input_returns_1_with_legacy_message(tmp_path: Path) -> None:
    output = tmp_path / "manifest.json"
    missing = tmp_path / "missing.mp4"
    stderr = StringIO()

    with redirect_stderr(stderr):
        exit_code = main(["transcribe", str(missing), "--output", str(output)])

    assert exit_code == 1
    assert stderr.getvalue().strip() == f"eve transcribe: input '{missing}' not found"


def test_transcribe_stub_writes_manifest_sources_only(tmp_path: Path) -> None:
    media = tmp_path / "clip.mp4"
    media.touch()
    output = tmp_path / "out" / "manifest.json"

    exit_code = main(["transcribe", "--output", str(output), "--stub", str(media)])

    payload = json.loads(output.read_text(encoding="utf-8"))
    assert exit_code == 0
    assert payload == {
        "version": "1.1",
        "sources": [
            {
                "id": "clip01",
                "file": "../clip.mp4",
                "timebase": {"numerator": 1, "denominator": 48000},
            }
        ],
        "segments": [],
        "render": {"filler_policy": "keep", "preserve_short_gaps": 0},
    }
    tjm_v11.validate_manifest(payload)


def test_transcribe_stub_mode_can_come_from_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    media = tmp_path / "clip.mp4"
    media.touch()
    output = tmp_path / "manifest.json"
    monkeypatch.setenv("VIDEO_TRANSCRIBE_STUB", "1")

    exit_code = main(["transcribe", str(media), "--output", str(output), "--pretty"])

    payload = json.loads(output.read_text(encoding="utf-8"))
    assert exit_code == 0
    assert (output.parent / payload["sources"][0]["file"]).resolve() == media.resolve()
    assert payload["segments"] == []
    tjm_v11.validate_manifest(payload)


def test_segment_to_dict_splits_long_segments_at_word_boundaries() -> None:
    segment = FakeSegment(
        start=0.0,
        end=2.0,
        text="alpha beta gamma delta",
        words=[
            FakeWord(0.0, 0.4, "alpha"),
            FakeWord(0.4, 0.8, "beta"),
            FakeWord(0.8, 1.2, "gamma"),
            FakeWord(1.2, 1.6, "delta"),
        ],
    )

    slices = transcribe.segment_to_dict("clip01", 1, segment, max_duration=0.7)

    assert [item["id"] for item in slices] == [
        "clip01-s0001-0",
        "clip01-s0001-1",
        "clip01-s0001-2",
        "clip01-s0001-3",
    ]
    assert slices[0]["text"] == "alpha"
    assert slices[0]["start"] == 0.0
    assert slices[0]["end"] == 0.4
    assert slices[1]["text"] == "beta"
    assert slices[1]["start"] == 0.4
    assert slices[1]["end"] == 0.8
    assert slices[0]["speaker"] is None
    assert slices[1]["speaker"] is None
    assert slices[2]["text"] == "gamma"
    assert slices[2]["start"] == 0.8
    assert slices[2]["end"] == 1.2
    assert slices[3]["text"] == "delta"
    assert slices[3]["start"] == 1.2
    assert slices[3]["end"] == 1.6


def test_ffmpeg_extract_invokes_expected_command(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls: list[tuple[object, object, object]] = []

    def fake_run(
        cmd: object,
        *,
        check: object,
        capture_output: object,
    ) -> None:
        calls.append((cmd, check, capture_output))

    monkeypatch.setattr("eve_cli.commands.transcribe.subprocess.run", fake_run)

    source = tmp_path / "input.mp4"
    output = tmp_path / "audio.wav"
    transcribe.ffmpeg_extract(source, output)

    assert len(calls) == 1
    cmd, check, capture_output = calls[0]
    assert cmd == [
        transcribe.FFMPEG,
        "-y",
        "-i",
        str(source),
        "-af",
        "aresample=async=1:first_pts=0",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-vn",
        str(output),
    ]
    assert check is True
    assert capture_output is True


def test_ffmpeg_extract_reports_missing_ffmpeg(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def fake_run(*_args: object, **_kwargs: object) -> None:
        raise FileNotFoundError("ffmpeg")

    monkeypatch.setattr("eve_cli.commands.transcribe.subprocess.run", fake_run)

    with pytest.raises(transcribe.MissingBackendDependencyError) as excinfo:
        transcribe.ffmpeg_extract(tmp_path / "input.mp4", tmp_path / "audio.wav")

    assert "ffmpeg executable not found" in str(excinfo.value)


def test_transcribe_run_uses_model_and_emits_manifest(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls: list[tuple[str, dict[str, object]]] = []
    extracted: list[tuple[Path, Path]] = []

    class FakeModel:
        def __init__(self, model: str, *, device: str, compute_type: str) -> None:
            assert model == "base.en"
            assert device == "auto"
            assert compute_type == "int8"

        def transcribe(
            self,
            wav_path: str,
            **kwargs: object,
        ) -> tuple[list[FakeSegment], None]:
            calls.append((wav_path, kwargs))
            return (
                [
                    FakeSegment(
                        start=0.0,
                        end=0.9,
                        text="ignored text",
                        words=[
                            FakeWord(0.0, 0.4, " hello "),
                            FakeWord(0.4, 0.9, "world"),
                            FakeWord(0.9, 1.0, "   "),
                        ],
                        speaker="spk1",
                    )
                ],
                None,
            )

    def fake_extract(input_path: Path, wav_path: Path) -> None:
        extracted.append((input_path, wav_path))

    monkeypatch.setattr(transcribe, "_load_whisper_model", lambda: FakeModel)
    monkeypatch.setattr(transcribe, "ffmpeg_extract", fake_extract)

    media = tmp_path / "clip.mp4"
    media.touch()
    output = tmp_path / "manifest.json"
    args = make_run_args(media, output)
    exit_code = transcribe.run(args)

    manifest_text = output.read_text(encoding="utf-8")
    payload = json.loads(manifest_text)

    assert exit_code == 0
    assert manifest_text.endswith("\n")
    assert extracted and extracted[0][0] == media
    assert calls == [
        (
            str(extracted[0][1]),
            {
                "beam_size": 5,
                "language": "en",
                "vad_filter": False,
                "word_timestamps": True,
            },
        )
    ]
    tjm_v11.validate_manifest(payload)
    assert payload["sources"] == [
        {
            "id": "clip01",
            "file": "clip.mp4",
            "timebase": {"numerator": 1, "denominator": 48000},
        }
    ]
    segment = payload["segments"][0]
    assert segment["id"] == "clip01-s0001"
    assert segment["speaker"] == "spk1"
    assert segment["spoken_text"] == segment["display_text"] == "hello world"
    assert (segment["start_tick"], segment["end_tick"]) == (0, 43200)
    assert [
        (word["spoken"], word["start_tick"], word["end_tick"])
        for word in segment["words"]
    ] == [("hello", 0, 19200), ("world", 19200, 43200)]
    assert segment["edit"] == {"tags": [], "notes": ""}


def test_transcribe_run_dispatches_default_backend_to_faster_whisper(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    dispatched: list[tuple[str, list[Path]]] = []
    returned_segments = [
        {
            "id": "clip01-s0001",
            "source": "clip01",
            "start": 0.0,
            "end": 0.5,
            "speaker": None,
            "text": "hello",
            "words": [{"start": 0.0, "end": 0.5, "token": "hello"}],
            "tags": [],
            "notes": "",
            "broll": None,
        }
    ]

    def fake_faster_whisper(
        args: Namespace,
        inputs: list[Path],
    ) -> list[dict[str, object]]:
        dispatched.append((args.backend, inputs))
        return returned_segments

    monkeypatch.setattr(transcribe, "transcribe_faster_whisper", fake_faster_whisper)

    media = tmp_path / "clip.mp4"
    media.touch()
    output = tmp_path / "manifest.json"

    exit_code = transcribe.run(make_run_args(media, output))
    payload = json.loads(output.read_text(encoding="utf-8"))

    assert exit_code == 0
    assert dispatched == [("faster-whisper", [media])]
    tjm_v11.validate_manifest(payload)
    assert payload["version"] == "1.1"
    assert payload["segments"][0]["spoken_text"] == "hello"
    assert payload["segments"][0]["words"][0]["end_tick"] == 24000
    assert "start_tick" not in returned_segments[0]
    assert returned_segments[0]["words"][0] == {
        "start": 0.0,
        "end": 0.5,
        "token": "hello",
    }


def test_transcribe_parser_accepts_hotwords_override() -> None:
    parser = build_parser()
    args = parser.parse_args(
        [
            "transcribe",
            "input.mp4",
            "--output",
            "manifest.json",
            "--hotwords",
            "um uh custom",
        ]
    )

    assert args.hotwords == "um uh custom"


def test_transcribe_run_verbatim_passes_prompt_controls(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls: list[dict[str, object]] = []

    class FakeModel:
        def __init__(self, model: str, *, device: str, compute_type: str) -> None:
            assert model == "base.en"
            assert device == "auto"
            assert compute_type == "int8"

        def transcribe(
            self,
            wav_path: str,
            **kwargs: object,
        ) -> tuple[list[FakeSegment], None]:
            assert wav_path.endswith(".wav")
            calls.append(kwargs)
            return ([], None)

    monkeypatch.setattr(transcribe, "_load_whisper_model", lambda: FakeModel)
    monkeypatch.setattr(transcribe, "ffmpeg_extract", lambda _input, _wav: None)

    media = tmp_path / "clip.mp4"
    media.touch()
    output = tmp_path / "manifest.json"

    exit_code = transcribe.run(make_run_args(media, output, verbatim=True))

    assert exit_code == 0
    assert calls == [
        {
            "beam_size": 5,
            "language": "en",
            "vad_filter": True,
            "word_timestamps": True,
            "hotwords": transcribe.DEFAULT_FILLER_HOTWORDS,
            "initial_prompt": transcribe.VERBATIM_INITIAL_PROMPT,
            "condition_on_previous_text": False,
            "temperature": 0.0,
        }
    ]


def test_transcribe_run_hotwords_override_replaces_default_verbatim_hotwords(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls: list[dict[str, object]] = []

    class FakeModel:
        def __init__(self, model: str, *, device: str, compute_type: str) -> None:
            pass

        def transcribe(
            self,
            wav_path: str,
            **kwargs: object,
        ) -> tuple[list[FakeSegment], None]:
            calls.append(kwargs)
            return ([], None)

    monkeypatch.setattr(transcribe, "_load_whisper_model", lambda: FakeModel)
    monkeypatch.setattr(transcribe, "ffmpeg_extract", lambda _input, _wav: None)

    media = tmp_path / "clip.mp4"
    media.touch()
    output = tmp_path / "manifest.json"

    exit_code = transcribe.run(
        make_run_args(media, output, verbatim=True, hotwords="um uh custom")
    )

    assert exit_code == 0
    assert calls[0]["hotwords"] == "um uh custom"


def test_transcribe_run_tags_fillers_in_manifest_when_enabled(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    class FakeModel:
        def __init__(self, model: str, *, device: str, compute_type: str) -> None:
            assert model == "base.en"
            assert device == "auto"
            assert compute_type == "int8"

        def transcribe(
            self,
            wav_path: str,
            **kwargs: object,
        ) -> tuple[list[FakeSegment], None]:
            assert wav_path.endswith(".wav")
            assert kwargs["word_timestamps"] is True
            return (
                [
                    FakeSegment(
                        start=0.0,
                        end=0.6,
                        text="um hello",
                        words=[
                            FakeWord(0.0, 0.2, " um "),
                            FakeWord(0.2, 0.6, "hello"),
                        ],
                    )
                ],
                None,
            )

    monkeypatch.setattr(transcribe, "_load_whisper_model", lambda: FakeModel)
    monkeypatch.setattr(transcribe, "ffmpeg_extract", lambda _input, _wav: None)

    media = tmp_path / "clip.mp4"
    media.touch()
    output = tmp_path / "manifest.json"

    exit_code = transcribe.run(make_run_args(media, output, tag_fillers=True))
    payload = json.loads(output.read_text(encoding="utf-8"))

    assert exit_code == 0
    tjm_v11.validate_manifest(payload)
    words = payload["segments"][0]["words"]
    assert [word["spoken"] for word in words] == ["um", "hello"]
    assert words[0]["edit"] == {"filler": True}
    assert "edit" not in words[1]


def test_transcribe_transformers_normalizes_pipeline_chunks(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    extracted: list[tuple[Path, Path]] = []
    pipeline_calls: list[tuple[str, dict[str, object]]] = []
    prompt_texts: list[str] = []

    class FakeCuda:
        @staticmethod
        def is_available() -> bool:
            return False

    class FakePipeline:
        tokenizer = SimpleNamespace(
            get_prompt_ids=lambda prompt: prompt_texts.append(prompt) or [101, 202]
        )

        def __call__(self, wav_path: str, **kwargs: object) -> dict[str, object]:
            pipeline_calls.append((wav_path, kwargs))
            return {
                "text": " hello world ",
                "chunks": [
                    {"text": " hello ", "timestamp": (0.0, 0.5)},
                    {"text": "world", "timestamp": (0.5, 1.1)},
                ],
            }

    def fake_pipeline(task: str, **kwargs: object) -> FakePipeline:
        assert task == "automatic-speech-recognition"
        assert kwargs == {"model": "base.en", "device": -1, "chunk_length_s": 30}
        return FakePipeline()

    def fake_import_module(name: str) -> object:
        if name == "torch":
            return SimpleNamespace(cuda=FakeCuda())
        if name == "transformers":
            return SimpleNamespace(pipeline=fake_pipeline)
        raise AssertionError(name)

    def fake_extract(input_path: Path, wav_path: Path) -> None:
        extracted.append((input_path, wav_path))

    monkeypatch.setattr(
        "eve_cli.commands.transcribe.importlib.import_module",
        fake_import_module,
    )
    monkeypatch.setattr(transcribe, "ffmpeg_extract", fake_extract)

    media = tmp_path / "clip.mp4"
    media.touch()

    segments = transcribe.transcribe_transformers(
        make_run_args(media, tmp_path / "manifest.json", backend="transformers"),
        [media],
    )

    assert extracted and extracted[0][0] == media
    assert pipeline_calls == [
        (
            str(extracted[0][1]),
            {"return_timestamps": "word", "generate_kwargs": {"language": "en"}},
        )
    ]
    assert prompt_texts == []
    assert segments == [
        {
            "id": "clip01-s0001",
            "source": "clip01",
            "start": 0.0,
            "end": 1.1,
            "speaker": None,
            "text": "hello world",
            "words": [
                {"start": 0.0, "end": 0.5, "token": "hello"},
                {"start": 0.5, "end": 1.1, "token": "world"},
            ],
            "tags": [],
            "notes": "",
            "broll": None,
        }
    ]


def test_transcribe_transformers_verbatim_adds_prompt_ids(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    extracted: list[tuple[Path, Path]] = []
    pipeline_calls: list[tuple[str, dict[str, object]]] = []
    prompt_texts: list[str] = []

    class FakeCuda:
        @staticmethod
        def is_available() -> bool:
            return False

    class FakePipeline:
        tokenizer = SimpleNamespace(
            get_prompt_ids=lambda prompt: prompt_texts.append(prompt) or [11, 22, 33]
        )

        def __call__(self, wav_path: str, **kwargs: object) -> dict[str, object]:
            pipeline_calls.append((wav_path, kwargs))
            return {
                "text": " uh ",
                "chunks": [{"text": " uh ", "timestamp": (0.0, 0.2)}],
            }

    def fake_pipeline(task: str, **kwargs: object) -> FakePipeline:
        assert task == "automatic-speech-recognition"
        assert kwargs == {"model": "base.en", "device": -1, "chunk_length_s": 30}
        return FakePipeline()

    def fake_import_module(name: str) -> object:
        if name == "torch":
            return SimpleNamespace(cuda=FakeCuda())
        if name == "transformers":
            return SimpleNamespace(pipeline=fake_pipeline)
        raise AssertionError(name)

    def fake_extract(input_path: Path, wav_path: Path) -> None:
        extracted.append((input_path, wav_path))

    monkeypatch.setattr(
        "eve_cli.commands.transcribe.importlib.import_module",
        fake_import_module,
    )
    monkeypatch.setattr(transcribe, "ffmpeg_extract", fake_extract)

    media = tmp_path / "clip.mp4"
    media.touch()

    segments = transcribe.transcribe_transformers(
        make_run_args(
            media,
            tmp_path / "manifest.json",
            backend="transformers",
            verbatim=True,
        ),
        [media],
    )

    assert extracted and extracted[0][0] == media
    assert prompt_texts == [transcribe.VERBATIM_INITIAL_PROMPT]
    assert pipeline_calls == [
        (
            str(extracted[0][1]),
            {
                "return_timestamps": "word",
                "generate_kwargs": {
                    "language": "en",
                    "prompt_ids": [11, 22, 33],
                },
            },
        )
    ]
    assert segments == [
        {
            "id": "clip01-s0001",
            "source": "clip01",
            "start": 0.0,
            "end": 0.2,
            "speaker": None,
            "text": "uh",
            "words": [{"start": 0.0, "end": 0.2, "token": "uh"}],
            "tags": [],
            "notes": "",
            "broll": None,
        }
    ]


def test_transcribe_nemo_normalizes_hypothesis_timestamps(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    extracted: list[tuple[Path, Path]] = []
    transcribe_calls: list[tuple[list[str], bool]] = []

    class FakeModel:
        def transcribe(
            self,
            paths: list[str],
            *,
            timestamps: bool,
        ) -> list[object]:
            transcribe_calls.append((paths, timestamps))
            return [
                SimpleNamespace(
                    text="hello world",
                    timestep={
                        "word": [
                            {"word": "hello", "start_offset": 0.0, "end_offset": 0.4},
                            {"word": "world", "start_offset": 0.4, "end_offset": 0.9},
                        ]
                    },
                )
            ]

    class FakeASRModel:
        @staticmethod
        def from_pretrained(*, model_name: str) -> FakeModel:
            assert model_name == "base.en"
            return FakeModel()

    def fake_import_module(name: str) -> object:
        if name == "nemo.collections.asr":
            return SimpleNamespace(models=SimpleNamespace(ASRModel=FakeASRModel))
        raise AssertionError(name)

    def fake_extract(input_path: Path, wav_path: Path) -> None:
        extracted.append((input_path, wav_path))

    monkeypatch.setattr(
        "eve_cli.commands.transcribe.importlib.import_module",
        fake_import_module,
    )
    monkeypatch.setattr(transcribe, "ffmpeg_extract", fake_extract)

    media = tmp_path / "clip.mp4"
    media.touch()

    segments = transcribe.transcribe_nemo(
        make_run_args(media, tmp_path / "manifest.json", backend="nemo"),
        [media],
    )

    assert extracted and extracted[0][0] == media
    assert transcribe_calls == [([str(extracted[0][1])], True)]
    assert segments == [
        {
            "id": "clip01-s0001",
            "source": "clip01",
            "start": 0.0,
            "end": 0.9,
            "speaker": None,
            "text": "hello world",
            "words": [
                {"start": 0.0, "end": 0.4, "token": "hello"},
                {"start": 0.4, "end": 0.9, "token": "world"},
            ],
            "tags": [],
            "notes": "",
            "broll": None,
        }
    ]


@pytest.mark.parametrize(
    ("backend", "missing_module", "extra"),
    [("transformers", "transformers", "transformers"), ("nemo", "nemo", "nemo")],
)
def test_transcribe_run_surfaces_backend_dependency_help(
    backend: str,
    missing_module: str,
    extra: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def fake_import_module(name: str) -> object:
        raise ModuleNotFoundError(f"No module named '{missing_module}'")

    monkeypatch.setattr(
        "eve_cli.commands.transcribe.importlib.import_module",
        fake_import_module,
    )

    media = tmp_path / "clip.mp4"
    media.touch()
    output = tmp_path / "manifest.json"
    stderr = StringIO()

    with redirect_stderr(stderr):
        exit_code = transcribe.run(make_run_args(media, output, backend=backend))

    message = stderr.getvalue().strip()
    assert exit_code == 2
    assert f"backend '{backend}' requires" in message
    assert f"uv sync --project cli --extra {extra}" in message
    assert f"uv pip install .[{extra}]" in message
    assert not output.exists()


def test_split_at_natural_boundaries_splits_at_sentence_end() -> None:
    words = [
        {"token": "Hello", "start": 0.0, "end": 0.3},
        {"token": "world.", "start": 0.3, "end": 0.6},
        {"token": "How", "start": 0.7, "end": 0.9},
        {"token": "are", "start": 0.9, "end": 1.1},
        {"token": "you?", "start": 1.1, "end": 1.4},
    ]
    groups = transcribe._split_at_natural_boundaries(words, max_words=12)
    assert len(groups) == 2
    assert [w["token"] for w in groups[0]] == ["Hello", "world."]
    assert [w["token"] for w in groups[1]] == ["How", "are", "you?"]


def test_split_at_natural_boundaries_merges_short_sentence_with_next() -> None:
    words = [
        {"token": "I", "start": 0.0, "end": 0.1},
        {"token": "agree", "start": 0.1, "end": 0.3},
        {"token": "completely.", "start": 0.3, "end": 0.6},
        {"token": "OK.", "start": 0.7, "end": 0.9},
        {"token": "Let", "start": 1.0, "end": 1.1},
        {"token": "us", "start": 1.1, "end": 1.2},
        {"token": "continue.", "start": 1.2, "end": 1.5},
    ]
    groups = transcribe._split_at_natural_boundaries(words, max_words=12)
    assert len(groups) == 2
    assert [w["token"] for w in groups[0]] == ["I", "agree", "completely."]
    assert [w["token"] for w in groups[1]] == ["OK.", "Let", "us", "continue."]


def test_split_at_natural_boundaries_splits_at_clause_when_long() -> None:
    words = [
        {"token": "The", "start": 0.0, "end": 0.1},
        {"token": "quick", "start": 0.1, "end": 0.2},
        {"token": "brown", "start": 0.2, "end": 0.3},
        {"token": "fox,", "start": 0.3, "end": 0.4},
        {"token": "the", "start": 0.5, "end": 0.6},
        {"token": "lazy", "start": 0.6, "end": 0.7},
        {"token": "dog,", "start": 0.7, "end": 0.8},
        {"token": "and", "start": 0.8, "end": 0.9},
        {"token": "then", "start": 0.9, "end": 1.0},
    ]
    groups = transcribe._split_at_natural_boundaries(words, max_words=8)
    assert len(groups) == 2
    assert [w["token"] for w in groups[0]] == [
        "The",
        "quick",
        "brown",
        "fox,",
        "the",
        "lazy",
        "dog,",
    ]
    assert [w["token"] for w in groups[1]] == ["and", "then"]


def test_split_at_natural_boundaries_trailing_short_merges_with_previous() -> None:
    words = [
        {"token": "One", "start": 0.0, "end": 0.1},
        {"token": "two", "start": 0.1, "end": 0.2},
        {"token": "three.", "start": 0.2, "end": 0.3},
        {"token": "Go", "start": 0.4, "end": 0.5},
    ]
    groups = transcribe._split_at_natural_boundaries(words, max_words=12)
    assert len(groups) == 1
    assert [w["token"] for w in groups[0]] == ["One", "two", "three.", "Go"]


def test_resegment_naturally_builds_new_segments() -> None:
    segments = [
        {
            "id": "clip01-s0001",
            "source": "clip01",
            "start": 0.0,
            "end": 2.0,
            "speaker": None,
            "text": "Hello world. How are you today?",
            "words": [
                {"token": "Hello", "start": 0.0, "end": 0.2},
                {"token": "world.", "start": 0.2, "end": 0.5},
                {"token": "How", "start": 0.6, "end": 0.8},
                {"token": "are", "start": 0.8, "end": 1.0},
                {"token": "you", "start": 1.0, "end": 1.2},
                {"token": "today?", "start": 1.2, "end": 2.0},
            ],
            "tags": [],
            "notes": "",
            "broll": None,
        }
    ]
    result = transcribe.resegment_naturally(segments, max_words=12)
    assert len(result) == 2
    assert result[0]["id"] == "clip01-s0001"
    assert result[0]["text"] == "Hello world."
    assert result[0]["start"] == 0.0
    assert result[0]["end"] == 0.5
    assert result[1]["id"] == "clip01-s0002"
    assert result[1]["text"] == "How are you today?"
    assert result[1]["start"] == 0.6
    assert result[1]["end"] == 2.0
