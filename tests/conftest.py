import os
import sys

# Tests see the same sounds on every machine: none of the real instruments
# (music/instruments.py) that this one may have downloaded.
os.environ["YS_INSTRUMENTS_DIR"] = os.path.join(os.path.dirname(__file__), "no-instruments")

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture(autouse=True)
def isolated_runtime(tmp_path, monkeypatch):
    """Keep state/overlay/feedback files out of the repo during tests."""
    import config

    monkeypatch.setattr(config, "RUNTIME_DIR", str(tmp_path / "runtime"))
    # Tracks don't pick up the acapellas on this machine (music/vocals.py).
    monkeypatch.setattr(config, "ACAPELLA_CHANCE", 0.0)
    yield tmp_path / "runtime"
