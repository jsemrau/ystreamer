import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import venv_guard  # noqa: E402


def pytest_configure():
    # Unlike the entry points, pytest can't re-exec itself (output capture is
    # already active by now), so refuse instead.
    if not venv_guard.in_venv():
        pytest.exit("run the tests in the project venv: ./venv/bin/python -m pytest",
                    returncode=pytest.ExitCode.USAGE_ERROR)


@pytest.fixture(autouse=True)
def isolated_runtime(tmp_path, monkeypatch):
    """Keep state/overlay/feedback files out of the repo during tests."""
    import config

    monkeypatch.setattr(config, "RUNTIME_DIR", str(tmp_path / "runtime"))
    yield tmp_path / "runtime"
