from __future__ import annotations

from typing import TypeAlias


JsonObject: TypeAlias = dict[str, object]
JsonArray: TypeAlias = list[object]

SUPPORTED_FILLER_POLICIES = {"keep", "drop"}
SUPPORTED_SEGMENT_KINDS = {"marker"}
SUPPORTED_WORD_KINDS = {"lexical", "filler"}
SUPPORTED_BROLL_MODES = {"replace", "pip"}
SUPPORTED_BROLL_AUDIO = {"source", "broll"}


class ValidationError(ValueError):
    pass


def validate_manifest(data: object) -> None:
    manifest = _expect_object(data, "manifest")
    _expect_exact_string(manifest.get("version"), "version", "1.1")

    sources = _expect_array(manifest.get("sources"), "sources")
    source_ids = _validate_sources(sources)

    render = _expect_object(manifest.get("render"), "render")
    _validate_render(render)

    segments = _expect_array(manifest.get("segments"), "segments")
    _validate_segments(segments, source_ids)


def _validate_sources(raw_sources: JsonArray) -> set[str]:
    source_ids: set[str] = set()
    for index, raw_source in enumerate(raw_sources):
        source = _expect_object(raw_source, f"sources[{index}]")
        source_id = _expect_non_empty_string(source.get("id"), f"sources[{index}].id")
        if source_id in source_ids:
            raise ValidationError(f"duplicate source id: {source_id}")
        source_ids.add(source_id)
        _expect_non_empty_string(source.get("file"), f"sources[{index}].file")

        timebase = _expect_object(source.get("timebase"), f"sources[{index}].timebase")
        numerator = _expect_positive_int(
            timebase.get("numerator"), f"sources[{index}].timebase.numerator"
        )
        denominator = _expect_positive_int(
            timebase.get("denominator"), f"sources[{index}].timebase.denominator"
        )
        if numerator <= 0 or denominator <= 0:
            raise ValidationError(f"invalid timebase in sources[{index}]")
    return source_ids


def _validate_render(render: JsonObject) -> None:
    filler_policy = _expect_non_empty_string(
        render.get("filler_policy"), "render.filler_policy"
    )
    if filler_policy not in SUPPORTED_FILLER_POLICIES:
        raise ValidationError(f"unsupported filler policy: {filler_policy}")

    preserve_short_gaps = _expect_number(
        render.get("preserve_short_gaps"), "render.preserve_short_gaps"
    )
    if preserve_short_gaps < 0:
        raise ValidationError("render.preserve_short_gaps must be >= 0")


def _validate_segments(raw_segments: JsonArray, source_ids: set[str]) -> None:
    segment_ids: set[str] = set()
    for index, raw_segment in enumerate(raw_segments):
        segment = _expect_object(raw_segment, f"segments[{index}]")
        segment_id = _expect_non_empty_string(
            segment.get("id"), f"segments[{index}].id"
        )
        if segment_id in segment_ids:
            raise ValidationError(f"duplicate segment id: {segment_id}")
        segment_ids.add(segment_id)

        kind_value = segment.get("kind")
        if kind_value is None:
            _validate_media_segment(segment, index, source_ids)
            continue

        kind = _expect_non_empty_string(kind_value, f"segments[{index}].kind")
        if kind not in SUPPORTED_SEGMENT_KINDS:
            raise ValidationError(f"unsupported segment kind: {kind}")
        _validate_marker_segment(segment, index, source_ids)


def _validate_media_segment(
    segment: JsonObject, index: int, source_ids: set[str]
) -> None:
    source = _expect_non_empty_string(
        segment.get("source"), f"segments[{index}].source"
    )
    if source not in source_ids:
        raise ValidationError(f"unknown source id: {source}")

    start_tick = _expect_int(segment.get("start_tick"), f"segments[{index}].start_tick")
    end_tick = _expect_int(segment.get("end_tick"), f"segments[{index}].end_tick")
    if end_tick <= start_tick:
        raise ValidationError(f"segments[{index}] has non-positive duration")

    words_value = segment.get("words")
    if words_value is not None:
        words = _expect_array(words_value, f"segments[{index}].words")
        for word_index, raw_word in enumerate(words):
            _validate_word(raw_word, index, word_index, start_tick, end_tick)

    broll_value = segment.get("broll")
    if broll_value is not None:
        _validate_broll(broll_value, f"segments[{index}].broll")


def _validate_marker_segment(
    segment: JsonObject, index: int, source_ids: set[str]
) -> None:
    title = segment.get("title")
    if title is not None:
        _expect_non_empty_string(title, f"segments[{index}].title")

    source = segment.get("source")
    if source is not None:
        source_id = _expect_non_empty_string(source, f"segments[{index}].source")
        if source_id not in source_ids:
            raise ValidationError(f"unknown source id: {source_id}")

    broll_value = segment.get("broll")
    if broll_value is not None:
        _validate_broll(broll_value, f"segments[{index}].broll")


def _validate_word(
    raw_word: object,
    segment_index: int,
    word_index: int,
    segment_start_tick: int,
    segment_end_tick: int,
) -> None:
    word = _expect_object(raw_word, f"segments[{segment_index}].words[{word_index}]")
    start_tick = _expect_int(
        word.get("start_tick"),
        f"segments[{segment_index}].words[{word_index}].start_tick",
    )
    end_tick = _expect_int(
        word.get("end_tick"), f"segments[{segment_index}].words[{word_index}].end_tick"
    )
    if end_tick <= start_tick:
        raise ValidationError(
            f"segments[{segment_index}].words[{word_index}] has non-positive duration"
        )
    if start_tick < segment_start_tick or end_tick > segment_end_tick:
        raise ValidationError(
            f"segments[{segment_index}].words[{word_index}] falls outside parent segment"
        )

    _expect_non_empty_string(
        word.get("spoken"), f"segments[{segment_index}].words[{word_index}].spoken"
    )

    kind_value = word.get("kind")
    if kind_value is not None:
        kind = _expect_non_empty_string(
            kind_value, f"segments[{segment_index}].words[{word_index}].kind"
        )
        if kind not in SUPPORTED_WORD_KINDS:
            raise ValidationError(f"unsupported word kind: {kind}")


def _validate_broll(raw_broll: object, path: str) -> None:
    broll = _expect_object(raw_broll, path)
    _expect_non_empty_string(broll.get("file"), f"{path}.file")

    mode_value = broll.get("mode")
    if mode_value is not None:
        mode = _expect_non_empty_string(mode_value, f"{path}.mode")
        if mode not in SUPPORTED_BROLL_MODES:
            raise ValidationError(f"unsupported broll mode: {mode}")

    audio_value = broll.get("audio")
    if audio_value is not None:
        audio = _expect_non_empty_string(audio_value, f"{path}.audio")
        if audio not in SUPPORTED_BROLL_AUDIO:
            raise ValidationError(f"unsupported broll audio: {audio}")

    if broll.get("still") is True and broll.get("audio") == "broll":
        raise ValidationError("still-image broll cannot use broll audio")


def _expect_object(value: object, path: str) -> JsonObject:
    if not isinstance(value, dict):
        raise ValidationError(f"{path} must be an object")
    return value


def _expect_array(value: object, path: str) -> JsonArray:
    if not isinstance(value, list):
        raise ValidationError(f"{path} must be an array")
    return value


def _expect_non_empty_string(value: object, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"{path} must be a non-empty string")
    return value


def _expect_exact_string(value: object, path: str, expected: str) -> str:
    actual = _expect_non_empty_string(value, path)
    if actual != expected:
        raise ValidationError(f"{path} must equal {expected!r}")
    return actual


def _expect_int(value: object, path: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValidationError(f"{path} must be an integer")
    return value


def _expect_positive_int(value: object, path: str) -> int:
    parsed = _expect_int(value, path)
    if parsed <= 0:
        raise ValidationError(f"{path} must be > 0")
    return parsed


def _expect_number(value: object, path: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValidationError(f"{path} must be a number")
    return float(value)
