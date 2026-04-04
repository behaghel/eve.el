from __future__ import annotations

import json
from argparse import Namespace
from contextlib import redirect_stderr
from io import StringIO
from pathlib import Path
from types import SimpleNamespace

import pytest

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
        "vad": False,
        "stub": False,
        "json": False,
        "command": "transcribe",
    }
    values.update(overrides)
    return Namespace(**values)


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
        "version": 1,
        "sources": [{"id": "clip01", "file": str(media)}],
        "segments": [],
    }


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
    assert payload["sources"][0]["file"] == str(media)
    assert payload["segments"] == []


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
        "-ac",
        "1",
        "-ar",
        "16000",
        "-vn",
        str(output),
    ]
    assert check is True
    assert capture_output is True


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
    assert payload["sources"] == [{"id": "clip01", "file": str(media)}]
    assert payload["segments"] == [
        {
            "id": "clip01-s0001",
            "source": "clip01",
            "start": 0.0,
            "end": 0.9,
            "speaker": "spk1",
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
    assert payload == {
        "version": 1,
        "sources": [{"id": "clip01", "file": str(media)}],
        "segments": returned_segments,
    }


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
            "initial_prompt": transcribe.VERBATIM_INITIAL_PROMPT,
            "condition_on_previous_text": False,
            "temperature": 0.0,
        }
    ]


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
    assert payload["segments"][0]["words"] == [
        {"start": 0.0, "end": 0.2, "token": "um", "kind": "filler"},
        {"start": 0.2, "end": 0.6, "token": "hello"},
    ]


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
