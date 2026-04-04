from __future__ import annotations

from eve_cli import tjm_v11


def _base_manifest() -> dict[str, object]:
    return {
        "version": "1.1",
        "sources": [
            {
                "id": "clip01",
                "file": "raw/interview.mp4",
                "timebase": {"numerator": 1, "denominator": 48000},
            }
        ],
        "render": {"filler_policy": "keep", "preserve_short_gaps": 0.0},
        "segments": [
            {
                "id": "clip01-s0001",
                "source": "clip01",
                "start_tick": 0,
                "end_tick": 48000,
                "display_text": "hello world",
                "words": [
                    {"start_tick": 0, "end_tick": 24000, "spoken": "hello"},
                    {"start_tick": 24000, "end_tick": 48000, "spoken": "world"},
                ],
            }
        ],
    }


def _first_segment(manifest: dict[str, object]) -> dict[str, object]:
    segments = manifest["segments"]
    assert isinstance(segments, list)
    segment = segments[0]
    assert isinstance(segment, dict)
    return segment


def _first_word(manifest: dict[str, object]) -> dict[str, object]:
    segment = _first_segment(manifest)
    words = segment["words"]
    assert isinstance(words, list)
    word = words[0]
    assert isinstance(word, dict)
    return word


def test_parse_manifest_migrates_legacy_flat_segment_edit_fields() -> None:
    payload = _base_manifest()
    segment = _first_segment(payload)
    segment["tags"] = ["intro"]
    segment["notes"] = "Use the tighter cut"
    segment["broll"] = {"file": "broll/card.mp4", "mode": "replace"}

    normalized = tjm_v11.parse_manifest(payload)
    normalized_segment = _first_segment(normalized)
    edit = normalized_segment["edit"]

    assert isinstance(edit, dict)
    assert edit["tags"] == ["intro"]
    assert edit["notes"] == "Use the tighter cut"
    assert edit["broll"] == {"file": "broll/card.mp4", "mode": "replace"}


def test_parse_manifest_accepts_nested_edit_fields_without_flat_aliases() -> None:
    payload = _base_manifest()
    segment = _first_segment(payload)
    segment["edit"] = {
        "tags": ["final"],
        "notes": None,
        "deleted": False,
        "broll": {"file": "broll/card.mp4", "mode": "pip", "audio": "source"},
    }
    word = _first_word(payload)
    word["edit"] = {"deleted": True}

    normalized = tjm_v11.parse_manifest(payload)
    normalized_segment = _first_segment(normalized)
    normalized_word = _first_word(normalized)

    assert normalized_segment["edit"] == {
        "tags": ["final"],
        "notes": None,
        "deleted": False,
        "broll": {"file": "broll/card.mp4", "mode": "pip", "audio": "source"},
    }
    assert normalized_word["edit"] == {"deleted": True}


def test_parse_manifest_migrates_legacy_word_kind_into_nested_edit_kind() -> None:
    payload = _base_manifest()
    word = _first_word(payload)
    word["kind"] = "filler"

    normalized = tjm_v11.parse_manifest(payload)
    normalized_word = _first_word(normalized)

    assert normalized_word["edit"] == {"kind": "filler"}
    assert "kind" not in normalized_word


def test_parse_manifest_prefers_nested_word_edit_kind_over_legacy_top_level_kind() -> (
    None
):
    payload = _base_manifest()
    word = _first_word(payload)
    word["kind"] = "lexical"
    word["edit"] = {"kind": "filler", "deleted": True}

    normalized = tjm_v11.parse_manifest(payload)
    normalized_word = _first_word(normalized)

    assert normalized_word["edit"] == {"kind": "filler", "deleted": True}
    assert "kind" not in normalized_word


def test_parse_manifest_prefers_nested_edit_values_over_legacy_flat_fields() -> None:
    payload = _base_manifest()
    segment = _first_segment(payload)
    segment["tags"] = ["legacy"]
    segment["notes"] = "legacy notes"
    segment["broll"] = {"file": "broll/legacy.mp4", "mode": "replace"}
    segment["edit"] = {
        "tags": ["nested"],
        "notes": "nested notes",
        "broll": {"file": "broll/nested.mp4", "mode": "pip", "audio": "source"},
        "deleted": True,
    }

    normalized = tjm_v11.parse_manifest(payload)
    normalized_segment = _first_segment(normalized)

    assert normalized_segment["edit"] == {
        "tags": ["nested"],
        "notes": "nested notes",
        "broll": {"file": "broll/nested.mp4", "mode": "pip", "audio": "source"},
        "deleted": True,
    }


def test_parse_manifest_removes_migrated_flat_fields_from_segment_root() -> None:
    payload = _base_manifest()
    segment = _first_segment(payload)
    segment["tags"] = []
    segment["notes"] = ""
    segment["broll"] = None

    normalized = tjm_v11.parse_manifest(payload)
    normalized_segment = _first_segment(normalized)

    assert "tags" not in normalized_segment
    assert "notes" not in normalized_segment
    assert "broll" not in normalized_segment
    assert normalized_segment["edit"] == {"tags": [], "notes": "", "broll": None}


def test_parse_manifest_preserves_segment_and_word_deleted_flags() -> None:
    payload = _base_manifest()
    segment = _first_segment(payload)
    segment["edit"] = {"deleted": True}
    word = _first_word(payload)
    word["edit"] = {"deleted": True}

    normalized = tjm_v11.parse_manifest(payload)
    normalized_segment = _first_segment(normalized)
    normalized_word = _first_word(normalized)

    assert normalized_segment["edit"] == {"deleted": True}
    assert normalized_word["edit"] == {"deleted": True}
