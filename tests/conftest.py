import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture(autouse=True)
def isolated_runtime(tmp_path, monkeypatch):
    """Keep state/overlay/feedback files out of the repo during tests."""
    import config

    monkeypatch.setattr(config, "RUNTIME_DIR", str(tmp_path / "runtime"))
    yield tmp_path / "runtime"
