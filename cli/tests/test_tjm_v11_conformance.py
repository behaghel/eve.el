from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from eve_cli import tjm_v11

FIXTURE_ROOT = Path(__file__).parent / "conformance" / "v1_1"
VALID_ROOT = FIXTURE_ROOT / "valid"
INVALID_ROOT = FIXTURE_ROOT / "invalid"


def _fixture_payload(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _fixture_paths(root: Path) -> list[Path]:
    return sorted(path for path in root.glob("*.json") if path.is_file())


@pytest.mark.parametrize(
    "fixture_path", _fixture_paths(VALID_ROOT), ids=lambda path: path.stem
)
def test_validate_manifest_accepts_valid_v11_examples(fixture_path: Path) -> None:
    payload = _fixture_payload(fixture_path)

    tjm_v11.validate_manifest(payload)


@pytest.mark.parametrize(
    "fixture_path", _fixture_paths(INVALID_ROOT), ids=lambda path: path.stem
)
def test_validate_manifest_rejects_invalid_v11_examples(fixture_path: Path) -> None:
    payload = _fixture_payload(fixture_path)

    with pytest.raises(tjm_v11.ValidationError):
        tjm_v11.validate_manifest(payload)


def test_validate_manifest_rejects_duplicate_source_ids() -> None:
    payload = _fixture_payload(VALID_ROOT / "minimal.json")
    payload["sources"].append(payload["sources"][0].copy())

    with pytest.raises(tjm_v11.ValidationError, match="duplicate source id: clip01"):
        tjm_v11.validate_manifest(payload)


def test_validate_manifest_rejects_negative_preserve_short_gaps() -> None:
    payload = _fixture_payload(VALID_ROOT / "minimal.json")
    payload["render"]["preserve_short_gaps"] = -0.1

    with pytest.raises(
        tjm_v11.ValidationError,
        match="render.preserve_short_gaps must be >= 0",
    ):
        tjm_v11.validate_manifest(payload)


def test_validate_manifest_rejects_marker_with_unknown_source() -> None:
    payload = _fixture_payload(VALID_ROOT / "minimal.json")
    payload["segments"][0] = {
        "id": "marker-001",
        "kind": "marker",
        "source": "missing",
        "title": "Recap",
    }

    with pytest.raises(tjm_v11.ValidationError, match="unknown source id: missing"):
        tjm_v11.validate_manifest(payload)


def test_validate_manifest_rejects_still_broll_with_broll_audio() -> None:
    payload = _fixture_payload(VALID_ROOT / "minimal.json")
    payload["segments"][0]["broll"] = {
        "file": "broll/card.png",
        "still": True,
        "audio": "broll",
    }

    with pytest.raises(
        tjm_v11.ValidationError,
        match="still-image broll cannot use broll audio",
    ):
        tjm_v11.validate_manifest(payload)


def test_validate_manifest_rejects_unsupported_word_kind() -> None:
    payload = _fixture_payload(VALID_ROOT / "minimal.json")
    payload["segments"][0]["words"][0]["kind"] = "noise"

    with pytest.raises(tjm_v11.ValidationError, match="unsupported word kind: noise"):
        tjm_v11.validate_manifest(payload)


def test_validate_manifest_rejects_unsupported_nested_word_kind() -> None:
    payload = _fixture_payload(VALID_ROOT / "minimal.json")
    payload["segments"][0]["words"][0]["edit"] = {"kind": "noise"}

    with pytest.raises(tjm_v11.ValidationError, match="unsupported word kind: noise"):
        tjm_v11.validate_manifest(payload)
