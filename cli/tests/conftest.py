"""Keep optional developer skips from silently weakening release media checks."""

from collections.abc import Generator

import pytest


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--require-media",
        action="store_true",
        help="Fail instead of skipping tests marked media (full health suite/CI).",
    )


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "media: requires real media dependencies; release checks forbid skips",
    )


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(
    item: pytest.Item,
) -> Generator[None, pytest.TestReport, pytest.TestReport]:
    report = yield
    if (
        item.config.getoption("--require-media")
        and item.get_closest_marker("media") is not None
        and report.skipped
    ):
        report.outcome = "failed"
        report.longrepr = f"Required media test skipped: {report.longrepr}"
    return report
