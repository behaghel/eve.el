from __future__ import annotations

from pathlib import Path


def cli_root() -> Path:
    return Path(__file__).resolve().parents[2]


def project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def package_entrypoint() -> Path:
    return project_root() / "eve.el"
