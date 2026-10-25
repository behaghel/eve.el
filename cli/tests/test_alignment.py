import json
import runpy
import sqlite3
import sys
import wave
from fractions import Fraction
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

from eve_cli import alignment


def word(index: int, text: str, start: str, end: str) -> alignment.WordAlignment:
    return alignment.WordAlignment(index, text, Fraction(start), Fraction(end))


def test_release_gate_is_80ms_and_40ms_is_reported_only_as_stretch() -> None:
    reference = [word(0, "one", "0.1", "1.0")]
    prediction = [word(0, "one", "0.18", "0.92")]
    report = alignment.evaluate(reference, prediction)
    assert report["tolerance_ms"] == 80
    assert report["within_tolerance"] == 2 and report["accuracy_pass"] is True
    assert report["stretch_tolerance_ms"] == 40
    assert report["stretch_within_tolerance"] == 0
    assert report["stretch_accuracy_pass"] is False
    failed = alignment.evaluate(reference, [word(0, "one", "0.180001", "0.92")])
    assert failed["accuracy_pass"] is False


def test_benchmark_ignores_sentence_punctuation_but_not_lexical_changes() -> None:
    reference = [word(0, "Hello,", "0.1", "1.0")]
    assert alignment.evaluate(reference, [word(0, "Hello", "0.1", "1.0")])[
        "accuracy_pass"
    ]
    with pytest.raises(alignment.AlignmentError, match="token"):
        alignment.evaluate(reference, [word(0, "Help", "0.1", "1.0")])


def test_benchmark_counts_every_boundary_and_exposes_outliers() -> None:
    reference = [word(0, "one", "0", "1"), word(1, "two", "2", "3")]
    predicted = [word(0, "one", "0.010", "1.015"), word(1, "two", "2.002", "3.100")]
    report = alignment.evaluate(reference, predicted)
    assert report["boundary_count"] == 4
    assert report["within_tolerance"] == 3
    assert report["within_fraction"] == 0.75
    assert report["p95_error_ms"] == 100
    assert report["max_error_ms"] == 100
    assert report["accuracy_pass"] is False


def test_benchmark_does_not_silently_drop_or_remap_words() -> None:
    reference = [word(0, "I", "0", "1"), word(1, "I", "2", "3")]
    with pytest.raises(alignment.AlignmentError, match="token"):
        alignment.evaluate(reference, reference[:1])
    with pytest.raises(alignment.AlignmentError, match="token"):
        alignment.evaluate(reference, [reference[0], word(0, "I", "2", "3")])


def test_known_silence_is_checked_on_actual_display_frames() -> None:
    reference = [word(0, "one", "0.1", "0.3"), word(1, "two", "0.6", "0.9")]
    predicted = [word(0, "one", "0.1", "0.32"), reference[1]]
    # A 20 ms error passes the distance gate, but frame 18 (0.3 s) is silent.
    report = alignment.evaluate(reference, predicted, frame_rate=Fraction(60))
    assert report["accuracy_pass"] is True
    assert report["silent_highlight_frames"] > 0
    assert report["pass"] is False


def test_silence_gate_uses_actual_pts_not_an_assumed_frame_grid() -> None:
    reference = [word(0, "one", "0.1", "0.3")]
    predicted = [word(0, "one", "0.1", "0.32")]
    # There is no video frame in the 20 ms overhang for this actual timeline.
    report = alignment.evaluate(
        reference,
        predicted,
        frame_times=[Fraction("0.1"), Fraction("0.2"), Fraction("0.333")],
    )
    assert report["silent_highlight_frames"] == 0
    assert report["pass"] is True
    # A variable-rate frame can expose the same overhang, off the 60 Hz grid.
    report = alignment.evaluate(
        reference, predicted, frame_times=[Fraction("0.1"), Fraction("0.305")]
    )
    assert report["silent_frame_indices"] == [1]
    assert report["pass"] is False
    for invalid in ([], [Fraction("0.2"), Fraction("0.1")]):
        with pytest.raises(alignment.AlignmentError, match="frame"):
            alignment.evaluate(reference, predicted, frame_times=invalid)


def test_benchmark_records_rejected_alignment_without_fabricated_accuracy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reference = tmp_path / "reference.json"
    reference.write_text(
        json.dumps(
            {
                "sources": [{"timebase": {"numerator": 1, "denominator": 48000}}],
                "segments": [
                    {
                        "words": [
                            {"spoken": "word", "start_tick": 4800, "end_tick": 9600}
                        ]
                    }
                ],
            }
        )
    )
    output = tmp_path / "result"

    def rejected(*args: Any, **kwargs: Any) -> Any:
        raise alignment.AlignmentError(
            "Qwen emitted non-monotonic timestamps; no repair applied"
        )

    monkeypatch.setattr(alignment, "align_qwen", rejected)
    monkeypatch.setattr(
        "eve_cli.commands.transcribe.ffmpeg_extract", lambda *args: None
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "benchmark",
            str(reference),
            "--audio",
            str(tmp_path / "audio.wav"),
            "--output-dir",
            str(output),
            "--engine",
            "qwen",
        ],
    )
    script = Path(__file__).resolve().parents[2] / "scripts/benchmark-alignment.py"
    assert runpy.run_path(str(script))["main"]() == 1
    report = json.loads((output / "report.json").read_text())
    assert report["status"] == "alignment_rejected"
    assert report["pass"] is False
    assert "within_tolerance" not in report and "words" not in report
    assert report["unspoken_word_probe"]["status"] == "not_run"


def test_alignment_normalization_keeps_original_token_ownership() -> None:
    vocabulary = {
        character: index
        for index, character in enumerate("ABCDEFGHIJKLMNOPQRSTUVWXYZ'|")
    }
    targets, owners = alignment.ctc_targets(["I'm", "Veriff,", "I", "I"], vocabulary)
    decoded = "".join(
        next(c for c, value in vocabulary.items() if value == target)
        for target in targets
    )
    assert decoded == "I'M|VERIFF|I|I"
    assert set(owner for owner in owners if owner is not None) == {0, 1, 2, 3}
    with pytest.raises(alignment.AlignmentError, match="25"):
        alignment.ctc_targets(["$25"], vocabulary)


@pytest.mark.parametrize(
    "text,expected",
    [
        ("0", "ZERO"),
        ("11,", "ELEVEN"),
        ("21", "TWENTY|ONE"),
        ("1,000.", "ONE|THOUSAND"),
    ],
)
def test_ctc_numbers_keep_one_original_owner(text: str, expected: str) -> None:
    vocabulary = {c: i for i, c in enumerate("ABCDEFGHIJKLMNOPQRSTUVWXYZ'|")}
    targets, owners = alignment.ctc_targets([text], vocabulary)
    reverse = {i: c for c, i in vocabulary.items()}
    assert "".join(reverse[i] for i in targets) == expected
    assert {owner for owner in owners if owner is not None} == {0}


@pytest.mark.parametrize(
    "text", ["001", "3.14", "-11", "$25", "10%", "11th", "11-year-old"]
)
def test_unsupported_numeric_forms_are_not_guessed(text: str) -> None:
    with pytest.raises(alignment.AlignmentError):
        alignment.ctc_targets(
            [text], {c: i for i, c in enumerate("ABCDEFGHIJKLMNOPQRSTUVWXYZ'|")}
        )


def test_numeric_readings_include_year_without_assuming_it() -> None:
    choices = alignment.ctc_readings("2027,")
    assert "TWENTY TWENTY SEVEN" in choices
    assert "TWO THOUSAND AND TWENTY SEVEN" in choices
    assert alignment.ctc_readings("11") == ("ELEVEN",)
    with pytest.raises(alignment.AlignmentError, match="alternatives"):
        alignment.ctc_reading_options(["2027"] * 8)


@pytest.mark.parametrize(
    "text,reading,uniform",
    [
        ("11,", "ELEVEN", False),
        ("21,", "TWENTY ONE", False),
        ("2027,", "TWENTY TWENTY SEVEN", False),
        ("2027,", "TWENTY TWENTY SEVEN", True),
    ],
)
def test_numeric_ctc_chooses_acoustic_reading_without_splitting_or_stretching(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    text: str,
    reading: str,
    uniform: bool,
) -> None:
    torch = pytest.importorskip("torch")
    pytest.importorskip("torchaudio")
    vocabulary = {c: i for i, c in enumerate("_ABCDEFGHIJKLMNOPQRSTUVWXYZ'|")}
    labels = [vocabulary[c] for c in reading.replace(" ", "|")]
    frames = 2 * len(labels) + 2
    logits = torch.full((1, frames, len(vocabulary)), -20.0)
    logits[:, :, 0] = 20
    for i, label in enumerate(labels):
        logits[0, 2 * i + 1, 0] = -20
        logits[0, 2 * i + 1, label] = 20
    if uniform:
        logits.zero_()
    path = tmp_path / "number.wav"
    with wave.open(str(path), "wb") as stream:
        stream.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        stream.writeframes(b"\1\0" * (frames * 320))
    calls = []

    class Model:
        config = SimpleNamespace(conv_stride=[320])

        def eval(self) -> Any:
            return self

        def __call__(self, **kwargs: Any) -> Any:
            calls.append(1)
            return SimpleNamespace(logits=logits)

    class Processor:
        tokenizer = SimpleNamespace(get_vocab=lambda: vocabulary, pad_token_id=0)

        def __call__(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
            return {}

    framework = SimpleNamespace(
        AutoProcessor=SimpleNamespace(from_pretrained=lambda *a, **kw: Processor()),
        AutoModelForCTC=SimpleNamespace(from_pretrained=lambda *a, **kw: Model()),
    )
    original_import = alignment.importlib.import_module
    monkeypatch.setattr(
        alignment.importlib,
        "import_module",
        lambda name, package=None: (
            framework if name == "transformers" else original_import(name, package)
        ),
    )
    if uniform:
        with pytest.raises(alignment.AlignmentError, match="unambiguous"):
            alignment.align_ctc(path, [text], offline=True)
        assert calls == [1]
        return
    result = alignment.align_ctc(path, [text], offline=True)
    assert len(result) == 1 and result[0].text == text and result[0].index == 0
    assert result[0].alignment_text == reading
    assert (result[0].start, result[0].end) == (
        Fraction(1, 50),
        Fraction(2 * len(labels), 50),
    )
    assert calls == [1]  # Pronunciation alternatives reuse the same model emissions.


def test_qwen_raw_timestamps_are_not_interpolated_or_monotonicity_repaired() -> None:
    aligned = alignment.qwen_intervals(["one", "two"], [1, 3, 4, 6], 80)
    assert (aligned[0].start, aligned[0].end) == (Fraction(2, 25), Fraction(6, 25))
    assert (aligned[1].start, aligned[1].end) == (Fraction(8, 25), Fraction(12, 25))
    with pytest.raises(alignment.AlignmentError, match="non-monotonic"):
        alignment.qwen_intervals(["one", "two"], [2, 3, 1, 4], 80)
    with pytest.raises(alignment.AlignmentError, match="zero"):
        alignment.qwen_intervals(["one"], [1, 1], 80)


@pytest.mark.parametrize(
    "engine",
    [
        alignment.align_ctc,
        alignment.align_ctc_boundary,
        alignment.align_ctc_phoneme,
        alignment.align_qwen,
        alignment.align_mfa,
    ],
)
def test_digitally_silent_audio_cannot_be_forced_into_spoken_words(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    engine: Any,
) -> None:
    path = tmp_path / "silent.wav"
    with wave.open(str(path), "wb") as file:
        file.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        file.writeframes(b"\0\0" * 16000)
    original_import = alignment.importlib.import_module

    def guarded_import(name: str, package: str | None = None) -> Any:
        if name in ("torch", "qwen_asr"):
            pytest.fail("Known silence must be rejected before model loading")
        return original_import(name, package)

    monkeypatch.setattr(alignment.importlib, "import_module", guarded_import)
    with pytest.raises(alignment.AlignmentError, match="No acoustic activity"):
        engine(path, ["imagined"])


@pytest.mark.parametrize("samples", [160000, 160001, 560123])
def test_ctc_windowed_inference_owns_every_native_frame_once(samples: int) -> None:
    torch = pytest.importorskip("torch")
    numpy = pytest.importorskip("numpy")
    waveform = numpy.arange(samples, dtype="float32")
    calls = []

    def processor(values: Any, **kwargs: Any) -> dict[str, Any]:
        first = int(values[0])
        assert kwargs["sampling_rate"] == 16000
        assert numpy.array_equal(values, waveform[first : first + len(values)])
        calls.append((first, len(values)))
        return {"input_values": torch.from_numpy(values).unsqueeze(0)}

    class Model:
        config = SimpleNamespace(conv_stride=[320], conv_kernel=[400])

        def __call__(self, input_values: Any) -> Any:
            first = int(input_values[0, 0])
            assert first % 320 == 0
            frames = (input_values.shape[1] - 400) // 320 + 1
            # Each emitted value identifies its original global frame. This
            # catches shifted, repeated, missing or interpolated ownership.
            values = torch.arange(first // 320, first // 320 + frames)
            return SimpleNamespace(logits=values.reshape(1, frames, 1))

    metadata = {"stale": True}
    result = alignment._ctc_emissions(
        Model(), processor, waveform, 16000, metadata=metadata
    )
    expected_frames = (samples - 400) // 320 + 1
    assert result.flatten().tolist() == list(range(expected_frames))
    assert metadata["native_frame_count"] == expected_frames
    assert metadata["window_count"] == len(calls)
    assert metadata["frame_ownership"] == "exact_native_grid"
    assert "stale" not in metadata
    windows = metadata["windows"]
    assert [
        (w["input_start_sample"], w["input_end_sample"] - w["input_start_sample"])
        for w in windows
    ] == calls
    assert all(length <= 160400 for _, length in calls)
    assert windows[0]["kept_frame_start"] == 0
    assert windows[-1]["kept_frame_end"] == expected_frames
    assert all(
        a["kept_frame_end"] == b["kept_frame_start"]
        for a, b in zip(windows, windows[1:])
    )
    if samples <= 160000:
        assert calls == [(0, samples)]
        assert metadata["strategy"] == "single-window"
    else:
        assert len(calls) > 1 and metadata["strategy"] == "overlap-windowed"


def test_ctc_window_frame_count_mismatch_is_rejected_not_resampled() -> None:
    torch = pytest.importorskip("torch")
    numpy = pytest.importorskip("numpy")

    class Model:
        config = SimpleNamespace(conv_stride=[320], conv_kernel=[400])

        def __call__(self, input_values: Any) -> Any:
            frames = (input_values.shape[1] - 400) // 320  # Deliberately one short.
            return SimpleNamespace(logits=torch.zeros((1, frames, 2)))

    def processor(values: Any, **kwargs: Any) -> dict[str, Any]:
        return {"input_values": torch.from_numpy(values).unsqueeze(0)}

    with pytest.raises(alignment.AlignmentError, match="frame"):
        alignment._ctc_emissions(
            Model(), processor, numpy.zeros(192000, dtype="float32"), 16000
        )


@pytest.mark.parametrize("word_boundaries", [False, True])
def test_ctc_adapter_preserves_emission_frame_ownership_without_model_downloads(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    word_boundaries: bool,
) -> None:
    torch = pytest.importorskip("torch")
    pytest.importorskip("torchaudio")
    path = tmp_path / "audio.wav"
    with wave.open(str(path), "wb") as file:
        file.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        file.writeframes(b"\1\0" * 2240)
    logits = torch.full((1, 7, 4), -20.0)
    for index, target in enumerate([0, 1, 0, 3, 0, 2, 3 if word_boundaries else 0]):
        logits[0, index, target] = 20

    class Model:
        config = SimpleNamespace(conv_stride=[320])

        def eval(self) -> Any:
            return self

        def __call__(self, **kwargs: Any) -> Any:
            return SimpleNamespace(logits=logits)

    class Processor:
        tokenizer = SimpleNamespace(
            get_vocab=lambda: {"_": 0, "A": 1, "B": 2, "|": 3}, pad_token_id=0
        )

        def __call__(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
            return {}

    framework = SimpleNamespace(
        AutoProcessor=SimpleNamespace(from_pretrained=lambda *a, **kw: Processor()),
        AutoModelForCTC=SimpleNamespace(from_pretrained=lambda *a, **kw: Model()),
    )
    original_import = alignment.importlib.import_module
    monkeypatch.setattr(
        alignment.importlib,
        "import_module",
        lambda name, package=None: (
            framework if name == "transformers" else original_import(name, package)
        ),
    )
    engine = alignment.align_ctc_boundary if word_boundaries else alignment.align_ctc
    aligned = engine(path, ["A", "B"], offline=True)
    assert [(word.index, word.text, word.start, word.end) for word in aligned] == [
        (
            0,
            "A",
            Fraction(1, 50),
            Fraction(2, 25) if word_boundaries else Fraction(1, 25),
        ),
        (
            1,
            "B",
            Fraction(1, 10),
            Fraction(7, 50) if word_boundaries else Fraction(3, 25),
        ),
    ]


def test_qwen_adapter_reads_raw_classes_without_invoking_sdk_repair(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    torch = pytest.importorskip("torch")
    path = tmp_path / "audio.wav"
    with wave.open(str(path), "wb") as file:
        file.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        file.writeframes(b"\1\0" * 16000)
    logits = torch.full((1, 7, 10), -20.0)
    for index, target in ((1, 1), (2, 3), (4, 4), (5, 6)):
        logits[0, index, target] = 20

    class Batch(dict[str, Any]):
        def to(self, *args: Any) -> Any:
            return self

    wrapper = SimpleNamespace(
        model=SimpleNamespace(
            device=torch.device("cpu"),
            dtype=torch.float32,
            thinker=lambda **kw: SimpleNamespace(logits=logits),
        ),
        aligner_processor=SimpleNamespace(
            encode_timestamp=lambda text, language: (text.split(), text),
            clean_token=lambda word: word,
        ),
        processor=lambda **kw: Batch(input_ids=torch.tensor([[0, 7, 7, 0, 7, 7, 0]])),
        timestamp_token_id=7,
        timestamp_segment_time=80,
    )
    qwen = SimpleNamespace(
        Qwen3ForcedAligner=SimpleNamespace(from_pretrained=lambda *a, **kw: wrapper)
    )
    hub = SimpleNamespace(snapshot_download=lambda *a, **kw: "/pinned-model-snapshot")
    original_import = alignment.importlib.import_module
    monkeypatch.setattr(
        alignment.importlib,
        "import_module",
        lambda name, package=None: (
            {"qwen_asr": qwen, "huggingface_hub": hub}.get(name)
            or original_import(name, package)
        ),
    )
    aligned = alignment.align_qwen(path, ["one", "two"], offline=True)
    assert [(word.start, word.end) for word in aligned] == [
        (Fraction(2, 25), Fraction(6, 25)),
        (Fraction(8, 25), Fraction(12, 25)),
    ]


def test_mfa_decodes_native_frame_indices_without_stretching_or_token_remapping() -> (
    None
):
    payload = {
        "frame_shift_ms": 10,
        "words": [
            {"text": "i", "start_frame": 3, "end_frame": 7, "score": -40.0},
            {"text": "i", "start_frame": 12, "end_frame": 18, "score": -42.0},
        ],
    }
    aligned = alignment.mfa_intervals(["I", "I"], payload)
    assert [(item.index, item.text, item.start, item.end) for item in aligned] == [
        (0, "I", Fraction(3, 100), Fraction(7, 100)),
        (1, "I", Fraction(3, 25), Fraction(9, 50)),
    ]
    assert aligned[0].acoustic_score == -40.0
    payload["words"][1]["text"] = "unknown"
    with pytest.raises(alignment.AlignmentError, match="token"):
        alignment.mfa_intervals(["I", "I"], payload)


def test_mfa_rejects_missing_zero_and_non_frame_timings() -> None:
    with pytest.raises(alignment.AlignmentError, match="token"):
        alignment.mfa_intervals(["word"], {"frame_shift_ms": 10, "words": []})
    for start, end in ((2, 2), (2.5, 4), (-1, 2)):
        with pytest.raises(alignment.AlignmentError, match="frame"):
            alignment.mfa_intervals(
                ["word"],
                {
                    "frame_shift_ms": 10,
                    "words": [
                        {"text": "word", "start_frame": start, "end_frame": end},
                    ],
                },
            )


@pytest.mark.parametrize("runner_fails", [False, True])
@pytest.mark.parametrize("speaker_adaptation", [False, True])
def test_mfa_adapter_isolated_pinned_and_does_not_mistake_runtime_failure_for_rejection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    runner_fails: bool,
    speaker_adaptation: bool,
) -> None:
    audio = tmp_path / "audio.wav"
    with wave.open(str(audio), "wb") as file:
        file.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        file.writeframes(b"\1\0" * 16000)
    calls = []

    def snapshot(*args: Any, **kwargs: Any) -> str:
        assert kwargs["revision"] == alignment.MFA_REVISION
        assert kwargs["local_files_only"] is True
        return str(tmp_path / "model")

    def run(command: list[str], **kwargs: Any) -> Any:
        assert "--locked" in command
        assert str(kwargs["env"]["MFA_ROOT_DIR"]).startswith(
            "/tmp/"
        ) or "eve-mfa-" in str(kwargs["env"]["MFA_ROOT_DIR"])
        calls.append(command)
        if any(arg.endswith("extract-frames.py") for arg in command):
            script_index = next(
                i for i, arg in enumerate(command) if arg.endswith("extract-frames.py")
            )
            assert ("--adapted" in command) is speaker_adaptation
            Path(command[script_index + 3]).write_text(
                json.dumps(
                    {
                        "frame_shift_ms": 10,
                        "words": [
                            {"text": "word", "start_frame": 3, "end_frame": 18},
                        ],
                    }
                )
            )
        return SimpleNamespace(
            returncode=1 if runner_fails else 0,
            stdout="",
            stderr="missing native library" if runner_fails else "",
        )

    original_import = alignment.importlib.import_module
    monkeypatch.setattr(
        alignment.importlib,
        "import_module",
        lambda name, package=None: (
            SimpleNamespace(snapshot_download=snapshot)
            if name == "huggingface_hub"
            else original_import(name, package)
        ),
    )
    # Patch the subprocess boundary, not a fake successful acoustic model.
    monkeypatch.setattr("subprocess.run", run)
    if runner_fails:
        with pytest.raises(RuntimeError, match="MFA"):
            alignment.align_mfa(
                audio, ["Word"], offline=True, speaker_adaptation=speaker_adaptation
            )
    else:
        result = alignment.align_mfa(
            audio, ["Word"], offline=True, speaker_adaptation=speaker_adaptation
        )
        assert (result[0].text, result[0].start, result[0].end) == (
            "Word",
            Fraction(3, 100),
            Fraction(9, 50),
        )
        assert "--no_textgrid_cleanup" in calls[0]
        assert ("--single_speaker" in calls[0]) is not speaker_adaptation
        assert "--g2p_model_path" in calls[0]


@pytest.mark.parametrize("batch", [False, True])
@pytest.mark.parametrize("adapted", [False, True])
def test_native_mfa_extraction_consumes_borrowed_scores_before_advancing_archive(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    batch: bool,
    adapted: bool,
) -> None:
    state = tmp_path / "state"
    (state / "alignment").mkdir(parents=True)
    prefix = "ali" if adapted else "ali_first_pass"
    (state / "alignment" / f"{prefix}.test.1.ark").touch()
    with sqlite3.connect(state / "corpus.db") as database:
        database.executescript("""
            CREATE TABLE utterance(id INT, speaker_id INT, file_id INT, begin FLOAT);
            INSERT INTO utterance VALUES(1, 1, 1, 0);
            CREATE TABLE file(id INT, name TEXT);
            INSERT INTO file VALUES(1, 'w0000'), (2, 'w0001');
            CREATE TABLE phone(kaldi_label TEXT, mapping_id INT);
            INSERT INTO phone VALUES('sil', 0), ('IY', 1);
            CREATE TABLE word(id INT, mapping_id INT, word TEXT);
            INSERT INTO word VALUES(0, 0, '<eps>'), (1, 1, 'i');
            CREATE TABLE pronunciation(word_id INT, pronunciation TEXT);
            INSERT INTO pronunciation VALUES(0, 'sil'), (1, 'IY');
        """)
        if batch:
            database.execute("INSERT INTO utterance VALUES(2, 1, 2, 0)")
    metadata = tmp_path / "meta.json"
    metadata.write_text(json.dumps({"features": {"frame_shift": 10}}))
    borrowed = SimpleNamespace(valid=False)

    class Scores:
        def numpy(self) -> Any:
            return self

        def __getitem__(self, key: Any) -> Any:
            return self

        def mean(self) -> float:
            if not borrowed.valid:
                raise RuntimeError("Native score storage was freed by archive advance")
            return -40.0

    class Archive:
        def __init__(self, *args: Any) -> None:
            assert Path(args[0]).name == f"{prefix}.test.1.ark"
            assert Path(args[1]).name == (
                "words.test.1.ark" if adapted else "words_first_pass.test.1.ark"
            )

        def __iter__(self) -> Any:
            for index in range(2 if batch else 1):
                borrowed.valid = True
                yield SimpleNamespace(
                    utterance_id=f"1-{index + 1}",
                    alignment=[1],
                    words=[1],
                    per_frame_likelihoods=Scores(),
                )
                borrowed.valid = False

        def close(self) -> None:
            borrowed.valid = False

    for name in (
        "_kalpy",
        "_kalpy.lat",
        "kalpy",
        "kalpy.gmm",
        "kalpy.gmm.data",
        "kalpy.models",
    ):
        module = ModuleType(name)
        module.__path__ = []
        monkeypatch.setitem(sys.modules, name, module)
    native = sys.modules["_kalpy.lat"]
    for name, value in {
        "CompactLatticeToWordAlignment": lambda _: (
            True,
            [1, 0, 1],
            [3, 7, 12],
            [4, 5, 6],
        ),
        "WordAlignLatticeLexiconInfo": lambda lexicon: lexicon,
        "WordAlignLatticeLexiconOpts": object,
        "linear_to_lattice": lambda *args: None,
        "word_align_lattice_lexicon": lambda *args: (True, None),
    }.items():
        monkeypatch.setattr(native, name, value, raising=False)
    monkeypatch.setattr(
        sys.modules["kalpy.gmm.data"], "AlignmentArchive", Archive, raising=False
    )
    monkeypatch.setattr(
        sys.modules["kalpy.models"],
        "AcousticModel",
        lambda _: SimpleNamespace(transition_model=None),
        raising=False,
    )
    script = (
        Path(__file__).resolve().parents[2] / "scripts/alignment-mfa/extract-frames.py"
    )
    extract = runpy.run_path(str(script))["extract"]
    result = extract(state, metadata, batch=batch, adapted=adapted)
    records = list(result["utterances"].values()) if batch else [result["words"]]
    if batch:
        assert set(result["utterances"]) == {"w0000", "w0001"}
    for entries in records:
        assert [
            (entry["text"], entry["start_frame"], entry["end_frame"])
            for entry in entries
        ] == [("i", 3, 7), ("i", 12, 18)]
        assert all(entry["score"] == -40.0 for entry in entries)


@pytest.mark.parametrize("override", [False, True])
def test_local_phoneme_adapter_preserves_pcm_context_and_atomic_token_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    override: bool,
) -> None:
    audio = tmp_path / "audio.wav"
    pcm = b"".join(
        value.to_bytes(2, "little", signed=True) for value in range(-24000, 24000)
    )
    with wave.open(str(audio), "wb") as clip:
        clip.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        clip.writeframes(pcm)
    seeds = [word(0, "One", "1", "1.3"), word(1, "Two", "1.5", "1.8")]
    monkeypatch.setattr(alignment, "align_ctc", lambda *args, **kwargs: seeds)
    monkeypatch.setattr(alignment, "_mfa_paths", lambda offline: (tmp_path, tmp_path))

    pronunciation_file = tmp_path / "confirmed.dict" if override else None
    if pronunciation_file:
        pronunciation_file.write_text("one\tW AH1 N\n")

    def native(
        corpus: Path,
        *args: Any,
        batch: bool = False,
        pronunciations: Path | None = None,
    ) -> dict[str, Any]:
        assert pronunciations == pronunciation_file
        assert batch is True
        for index in range(2):
            assert (corpus / f"w{index:04d}.lab").read_text() == "one two\n"
            with wave.open(str(corpus / f"w{index:04d}.wav")) as clip:
                assert clip.getframerate() == 16000
                assert clip.readframes(clip.getnframes()) == pcm[12800 * 2 : 32000 * 2]
        entries = [
            {"text": "one", "start_frame": 20, "end_frame": 50},
            {"text": "two", "start_frame": 70, "end_frame": 100},
        ]
        return {
            "frame_shift_ms": 10,
            "utterances": {"w0000": entries, "w0001": entries},
        }

    monkeypatch.setattr(alignment, "_run_mfa", native)
    assert (
        alignment.align_ctc_phoneme(
            audio, ["One", "Two"], offline=True, pronunciations=pronunciation_file
        )
        == seeds
    )


def test_pronunciation_override_keeps_lt_atomic_and_never_edits_inputs(
    tmp_path: Path,
) -> None:
    base, override, metadata, output = [
        tmp_path / name
        for name in ("base.dict", "confirmed.dict", "meta.json", "derived.dict")
    ]
    base.write_text("lt\tL T\nteam\tT IY1 M\n")
    override.write_text("LT\tEH1 L T IY1\n")
    metadata.write_text(json.dumps({"phones": ["EH1", "L", "T", "IY1", "M"]}))
    before = (base.read_bytes(), override.read_bytes())
    alignment.mfa_pronunciation_dictionary(base, override, metadata, output)
    assert output.read_text().splitlines() == ["team\tT IY1 M", "lt\tEH1 L T IY1"]
    assert (base.read_bytes(), override.read_bytes()) == before
    result = alignment.mfa_intervals(
        ["LT"],
        {
            "frame_shift_ms": 10,
            "words": [{"text": "lt", "start_frame": 10, "end_frame": 30}],
        },
    )
    assert len(result) == 1 and result[0].text == "LT"


@pytest.mark.parametrize("contents", ["lt\tUNKNOWN\n", "lt\t\n", "LT\tL T\nlt\tL T\n"])
def test_bad_pronunciation_configuration_is_not_word_rejection(
    tmp_path: Path, contents: str
) -> None:
    base, override, metadata, output = [
        tmp_path / name
        for name in ("base.dict", "override.dict", "meta.json", "derived.dict")
    ]
    base.write_text("team\tT IY1 M\n")
    override.write_text(contents)
    metadata.write_text(json.dumps({"phones": ["L", "T", "IY1", "M"]}))
    with pytest.raises(ValueError) as error:
        alignment.mfa_pronunciation_dictionary(base, override, metadata, output)
    assert not isinstance(error.value, alignment.AlignmentError)
    assert not output.exists()


def test_pronunciation_override_cannot_overwrite_model_dictionary(
    tmp_path: Path,
) -> None:
    base, override, metadata = [
        tmp_path / name for name in ("base.dict", "override.dict", "meta.json")
    ]
    base.write_text("lt\tL T\n")
    override.write_text("lt\tL T\n")
    metadata.write_text(json.dumps({"phones": ["L", "T"]}))
    with pytest.raises(ValueError, match="overwrite"):
        alignment.mfa_pronunciation_dictionary(base, override, metadata, base)
    assert base.read_text() == "lt\tL T\n"


def test_local_phoneme_windows_are_context_not_reported_word_boundaries() -> None:
    seeds = [word(0, "one", "1", "1.3"), word(1, "two", "1.5", "1.8")]
    windows = alignment.phoneme_windows(seeds, 1000, 3000)
    assert windows == [(800, 1500), (1300, 2000)]
    payload = {
        "frame_shift_ms": 10,
        "utterances": {
            "w0000": [{"text": "one", "start_frame": 20, "end_frame": 50}],
            "w0001": [{"text": "two", "start_frame": 20, "end_frame": 50}],
        },
    }
    restored = alignment.restore_phoneme_windows(seeds, windows, payload, 1000)
    assert restored == seeds  # Neither context edges nor gaps became timings.
    payload["utterances"]["w0000"][0]["start_frame"] = 0
    with pytest.raises(alignment.AlignmentError, match="censored"):
        alignment.restore_phoneme_windows(seeds, windows, payload, 1000)


def test_contextual_phonemes_use_neighbors_but_publish_only_central_word() -> None:
    seeds = [
        word(0, "one", "1", "1.3"),
        word(1, "two", "1.5", "1.8"),
        word(2, "three", "2", "2.2"),
    ]
    windows = alignment.phoneme_windows(seeds, 1000, 3000, neighbors=1)
    assert windows == [(800, 2000), (800, 2400), (1300, 2400)]

    def entry(text: str, start: int, end: int) -> dict[str, Any]:
        return {"text": text, "start_frame": start, "end_frame": end}

    payload = {
        "frame_shift_ms": 10,
        "utterances": {
            "w0000": [entry("one", 20, 50), entry("two", 70, 100)],
            "w0001": [
                entry("one", 0, 50),
                entry("two", 70, 100),
                entry("three", 120, 140),
            ],
            "w0002": [entry("two", 20, 50), entry("three", 70, 90)],
        },
    }
    # A clipped *context* word is never published in place of the target.
    assert (
        alignment.restore_phoneme_windows(seeds, windows, payload, 1000, neighbors=1)
        == seeds
    )


def test_local_phoneme_rejects_anchor_conflicts_and_cross_word_overlap() -> None:
    seeds = [word(0, "one", "1", "1.3"), word(1, "two", "1.5", "1.8")]
    windows = [(0, 2000), (1000, 3000)]
    payload = {
        "frame_shift_ms": 10,
        "utterances": {
            "w0000": [{"text": "one", "start_frame": 100, "end_frame": 110}],
            "w0001": [{"text": "two", "start_frame": 50, "end_frame": 80}],
        },
    }
    with pytest.raises(alignment.AlignmentError, match="anchor"):
        alignment.restore_phoneme_windows(seeds, windows, payload, 1000)
    payload["utterances"]["w0000"][0]["end_frame"] = 160
    with pytest.raises(alignment.AlignmentError, match="overlap") as rejected:
        alignment.restore_phoneme_windows(seeds, windows, payload, 1000)
    assert len(rejected.value.diagnostics["raw_target_intervals"]) == 2
    assert rejected.value.diagnostics["accepted"] is False


def test_quiet_edge_refinement_uses_signal_boundaries_not_fixed_padding() -> None:
    # Clearly separated acoustic regions, deliberately asymmetric word tails.
    audio = [0.0] * 200 + [0.1] * 230 + [0.0] * 200 + [0.1] * 370 + [0.0] * 200
    seeds = [word(0, "one", "0.22", "0.35"), word(1, "two", "0.66", "0.90")]
    refined = alignment.refine_quiet_edges(seeds, audio, 1000)
    assert [(item.start, item.end) for item in refined] == [
        (Fraction("0.2"), Fraction("0.43")),
        (Fraction("0.63"), Fraction("1.0")),
    ]
    assert [(item.index, item.text) for item in refined] == [(0, "one"), (1, "two")]


def test_quiet_refinement_keeps_unsupported_boundaries() -> None:
    seeds = [word(0, "one", "0.2", "0.4"), word(1, "two", "0.5", "0.7")]
    for samples in ([0.1] * 1000, [0.0] * 1000):
        assert alignment.refine_quiet_edges(seeds, samples, 1000) == seeds
    # Shared connected-speech activity cannot be split into separate words by RMS.
    samples = [0.0] * 100 + [0.1] * 700 + [0.0] * 200
    result = alignment.refine_quiet_edges(seeds, samples, 1000)
    assert result[0].end == seeds[0].end
    assert result[1].start == seeds[1].start


def test_provisional_pause_diagnostic_protects_only_clear_pause_interiors() -> None:
    predictions = [word(0, "edge", "0.18", "0.28"), word(1, "inside", "0.29", "0.31")]
    # Silence .2..45 is exactly 250 ms; its protected interior is .28..37.
    # The first word merely touches that interior and must not be flagged.
    observed = alignment.pause_overlaps(
        predictions, [(0, 200), (450, 1000)], 1000, 1000
    )
    assert [item["word_index"] for item in observed] == [1]
    assert observed[0]["protected_start"] == 0.28
    assert observed[0]["protected_end"] == 0.37
    assert (
        alignment.pause_overlaps(predictions, [(0, 200), (449, 1000)], 1000, 1000) == []
    )
    assert predictions[1].start == Fraction("0.29")


def test_model_frame_clock_is_rational_not_stretched_to_audio_duration() -> None:
    clock = alignment.FrameClock(sample_rate=16000, hop_samples=320)
    assert clock.interval(13, 35) == (Fraction(13, 50), Fraction(7, 10))
    assert clock.interval(0, 1) == (Fraction(0), Fraction(1, 50))
