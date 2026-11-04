from __future__ import annotations

import json
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

from eve_cli.main import main


def test_doctor_json_reports_expected_paths() -> None:
    stdout = StringIO()
    with redirect_stdout(stdout):
        exit_code = main(["doctor", "--json"])

    payload = json.loads(stdout.getvalue())

    assert exit_code == 0
    assert payload["ok"] is True
    assert payload["command"] == "doctor"
    assert Path(payload["project_root"]) == Path(__file__).resolve().parents[2]
    assert Path(payload["package_entrypoint"]).name == "eve.el"
    assert payload["package_entrypoint_present"] is True
