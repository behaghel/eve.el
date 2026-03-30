from __future__ import annotations

import json
from pathlib import Path

import pytest

from eve_cli import tjm_v11


FIXTURE_ROOT = Path(__file__).parent / "conformance" / "v1_1"
VALID_ROOT = FIXTURE_ROOT / "valid"
INVALID_ROOT = FIXTURE_ROOT / "invalid"


def _fixture_paths(root: Path) -> list[Path]:
    return sorted(path for path in root.glob("*.json") if path.is_file())


@pytest.mark.parametrize(
    "fixture_path", _fixture_paths(VALID_ROOT), ids=lambda path: path.stem
)
def test_validate_manifest_accepts_valid_v11_examples(fixture_path: Path) -> None:
    payload = json.loads(fixture_path.read_text(encoding="utf-8"))

    tjm_v11.validate_manifest(payload)


@pytest.mark.parametrize(
    "fixture_path", _fixture_paths(INVALID_ROOT), ids=lambda path: path.stem
)
def test_validate_manifest_rejects_invalid_v11_examples(fixture_path: Path) -> None:
    payload = json.loads(fixture_path.read_text(encoding="utf-8"))

    with pytest.raises(tjm_v11.ValidationError):
        tjm_v11.validate_manifest(payload)
