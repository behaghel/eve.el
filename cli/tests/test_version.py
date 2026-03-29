from __future__ import annotations

import pytest

from eve_cli.main import main


def test_version_flag_exits_cleanly(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["--version"])

    captured = capsys.readouterr()

    assert excinfo.value.code == 0
    assert captured.out.startswith("eve 0.1.0")
