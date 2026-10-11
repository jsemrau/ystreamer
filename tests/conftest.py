import os
import sys

# Tests see the same sounds on every machine: none of the real instruments
# (music/instruments.py) that this one may have downloaded.
os.environ["YS_INSTRUMENTS_DIR"] = os.path.join(os.path.dirname(__file__), "no-instruments")
# ... and the stock drums and bass (music/kits.py), not this machine's kits.
os.environ["YS_DRUM_KITS"] = "0"

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture(autouse=True)
def isolated_runtime(tmp_path, monkeypatch):
    """Keep state/overlay/feedback files out of the repo during tests."""
    import config

    monkeypatch.setattr(config, "RUNTIME_DIR", str(tmp_path / "runtime"))
    # Tracks don't pick up the acapellas on this machine (music/vocals.py).
    monkeypatch.setattr(config, "ACAPELLA_CHANCE", 0.0)
    # ... and explore as often as by default, whatever this machine's .env says (music/explore.py).
    monkeypatch.setattr(config, "EXPLORE", 0.5)
    yield tmp_path / "runtime"
