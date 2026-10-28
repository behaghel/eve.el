"""Exercise the release-media gate in isolated pytest sessions."""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize(
    ("required", "media", "skip", "status"),
    [
        (False, True, True, 0),
        (True, True, True, 1),
        (True, False, True, 0),
        (True, True, False, 0),
    ],
    ids=["developer-skip", "required-media-skip", "platform-skip", "media-pass"],
)
def test_media_gate(
    tmp_path: Path, required: bool, media: bool, skip: bool, status: int
) -> None:
    plugin = Path(__file__).with_name("conftest.py")
    if plugin.exists():
        shutil.copyfile(plugin, tmp_path / "conftest.py")
    (tmp_path / "test_fixture.py").write_text(
        "import pytest\n"
        + ("@pytest.mark.media\n" if media else "")
        + "def test_fixture():\n"
        + (
            "    pytest.skip('simulated unavailable dependency or platform')\n"
            if skip
            else "    assert True\n"
        ),
        encoding="utf-8",
    )
    env = dict(os.environ)
    env.pop("PYTEST_ADDOPTS", None)
    env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-rs"]
        + (["--require-media"] if required else []),
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    output = result.stdout + result.stderr
    assert result.returncode == status, output
    if required and media and skip:
        assert "Required media test skipped" in output
        assert "simulated unavailable dependency or platform" in output
