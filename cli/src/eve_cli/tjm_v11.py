from __future__ import annotations

from copy import deepcopy

type JsonObject = dict[str, object]
type JsonArray = list[object]

LEGACY_SEGMENT_EDIT_FIELDS = ("broll", "tags", "notes")
SUPPORTED_FILLER_POLICIES = {"keep", "drop"}
SUPPORTED_SEGMENT_KINDS = {"marker"}
SUPPORTED_WORD_KINDS = {"lexical", "filler"}
SUPPORTED_BROLL_MODES = {"replace", "pip"}
SUPPORTED_BROLL_AUDIO = {"source", "broll"}
MISSING = object()


class ValidationError(ValueError):
    pass


def parse_manifest(data: object) -> JsonObject:
    manifest = _expect_object(data, "manifest")
    normalized = _normalize_manifest(manifest)

    _expect_exact_string(normalized.get("version"), "version", "1.1")

    sources = _expect_array(normalized.get("sources"), "sources")
    source_ids = _validate_sources(sources)

    render = _expect_object(normalized.get("render"), "render")
    _validate_render(render)

    segments = _expect_array(normalized.get("segments"), "segments")
    _validate_segments(segments, source_ids)

    return normalized


def validate_manifest(data: object) -> None:
    parse_manifest(data)


def _normalize_manifest(manifest: JsonObject) -> JsonObject:
    normalized = _copy_object(manifest)
    segments_value = normalized.get("segments")
    if segments_value is None:
        return normalized

    segments = _expect_array(segments_value, "segments")
    normalized["segments"] = [
        _normalize_segment(raw_segment, index)
        for index, raw_segment in enumerate(segments)
    ]
    return normalized


def _normalize_segment(raw_segment: object, index: int) -> JsonObject:
    path = f"segments[{index}]"
    segment = _copy_object(_expect_object(raw_segment, path))

    edit = _normalize_segment_edit(segment, path)
    if edit is not None:
        segment["edit"] = edit

    words_value = segment.get("words")
    if words_value is not None:
        words = _expect_array(words_value, f"{path}.words")
        segment["words"] = [
            _normalize_word(raw_word, index, word_index)
            for word_index, raw_word in enumerate(words)
        ]

    return segment


def _normalize_segment_edit(segment: JsonObject, path: str) -> JsonObject | None:
    legacy_values: JsonObject = {}
    for field in LEGACY_SEGMENT_EDIT_FIELDS:
        if field in segment:
            legacy_values[field] = segment.pop(field)

    nested_edit_value = segment.get("edit")
    if nested_edit_value is None and not legacy_values:
        return None

    edit: JsonObject = {}
    if nested_edit_value is not None:
        edit = _copy_object(_expect_object(nested_edit_value, f"{path}.edit"))

    for field, value in legacy_values.items():
        if field not in edit:
            edit[field] = value

    return edit


def _normalize_word(
    raw_word: object, segment_index: int, word_index: int
) -> JsonObject:
    path = f"segments[{segment_index}].words[{word_index}]"
    word = _copy_object(_expect_object(raw_word, path))

    legacy_kind = word.pop("kind", MISSING)
    edit_value = word.get("edit")
    if edit_value is not None or legacy_kind is not MISSING:
        edit: JsonObject = {}
        if edit_value is not None:
            edit = _copy_object(_expect_object(edit_value, f"{path}.edit"))
        if legacy_kind is not MISSING and "kind" not in edit:
            edit["kind"] = legacy_kind
        word["edit"] = edit

    return word


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

    _validate_segment_edit(segment.get("edit"), f"segments[{index}].edit")


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

    _validate_segment_edit(segment.get("edit"), f"segments[{index}].edit")


def _validate_segment_edit(raw_edit: object, path: str) -> None:
    if raw_edit is None:
        return

    edit = _expect_object(raw_edit, path)

    if "deleted" in edit:
        _expect_bool(edit["deleted"], f"{path}.deleted")

    if "tags" in edit:
        _validate_string_array(edit["tags"], f"{path}.tags")

    if "notes" in edit:
        _expect_string_or_null(edit["notes"], f"{path}.notes")

    if "broll" in edit and edit["broll"] is not None:
        _validate_broll(edit["broll"], f"{path}.broll")


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
            f"segments[{segment_index}].words[{word_index}] "
            "falls outside parent segment"
        )

    _expect_non_empty_string(
        word.get("spoken"), f"segments[{segment_index}].words[{word_index}].spoken"
    )

    edit = word.get("edit")
    kind_path = f"segments[{segment_index}].words[{word_index}].kind"
    if isinstance(edit, dict) and "kind" in edit:
        kind_path = f"segments[{segment_index}].words[{word_index}].edit.kind"

    kind_value = _effective_word_kind(word)
    if kind_value is not None:
        kind = _expect_non_empty_string(kind_value, kind_path)
        if kind not in SUPPORTED_WORD_KINDS:
            raise ValidationError(f"unsupported word kind: {kind}")

    _validate_word_edit(
        word.get("edit"), f"segments[{segment_index}].words[{word_index}].edit"
    )


def _validate_word_edit(raw_edit: object, path: str) -> None:
    if raw_edit is None:
        return

    edit = _expect_object(raw_edit, path)
    if "kind" in edit:
        _expect_non_empty_string(edit["kind"], f"{path}.kind")
    if "deleted" in edit:
        _expect_bool(edit["deleted"], f"{path}.deleted")


def _effective_word_kind(word: JsonObject) -> object:
    edit = word.get("edit")
    if isinstance(edit, dict) and "kind" in edit:
        return edit["kind"]
    return word.get("kind")


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


def _copy_object(value: JsonObject) -> JsonObject:
    copied: JsonObject = deepcopy(value)
    return copied


def _validate_string_array(value: object, path: str) -> None:
    items = _expect_array(value, path)
    for index, item in enumerate(items):
        _expect_string(item, f"{path}[{index}]")


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


def _expect_string(value: object, path: str) -> str:
    if not isinstance(value, str):
        raise ValidationError(f"{path} must be a string")
    return value


def _expect_string_or_null(value: object, path: str) -> str | None:
    if value is not None and not isinstance(value, str):
        raise ValidationError(f"{path} must be a string or null")
    return value


def _expect_exact_string(value: object, path: str, expected: str) -> str:
    actual = _expect_non_empty_string(value, path)
    if actual != expected:
        raise ValidationError(f"{path} must equal {expected!r}")
    return actual


def _expect_bool(value: object, path: str) -> bool:
    if not isinstance(value, bool):
        raise ValidationError(f"{path} must be a boolean")
    return value


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
