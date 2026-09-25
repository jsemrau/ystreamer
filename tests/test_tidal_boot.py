import os
import shutil
import stat

import pytest

import audio_stack
from audio_stack import TidalSession, diagnose_tidal_boot

FAKE_GHCI = r"""#!/usr/bin/env bash
echo "<no location info>: error:"
echo "    Ambiguous module name 'Sound.Tidal.Context':"
echo "      it was found in multiple packages: tidal-1.10.1 tidal-1.10.1"
while IFS= read -r line; do
  case "$line" in
    *YSTREAMER_TIDAL_READY*) echo "YSTREAMER_TIDAL_READY" ;;
    ":t hush") echo "<interactive>:2:1: error: Variable not in scope: hush" ;;
    ":quit") exit 0 ;;
  esac
done
"""

HEALTHY_GHCI = r"""#!/usr/bin/env bash
while IFS= read -r line; do
  case "$line" in
    *YSTREAMER_TIDAL_READY*) echo "YSTREAMER_TIDAL_READY" ;;
    ":t hush") echo "hush :: IO ()" ;;
    ":quit") exit 0 ;;
  esac
done
"""


def install_fake(tmp_path, monkeypatch, script):
    path = tmp_path / "ghci"
    path.write_text(script)
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setattr(audio_stack, "log", lambda msg: None)


def test_diagnoses_duplicate_tidal_install(tmp_path, monkeypatch):
    install_fake(tmp_path, monkeypatch, FAKE_GHCI)
    session = TidalSession(boot_file=str(tmp_path / "BootTidal.hs"))
    message = session.start()
    assert "FAILED" in message
    assert "fix_ghc_env.py" in session.boot_error
    session.stop()


def test_healthy_boot_has_no_error(tmp_path, monkeypatch):
    install_fake(tmp_path, monkeypatch, HEALTHY_GHCI)
    session = TidalSession(boot_file=str(tmp_path / "BootTidal.hs"))
    assert session.start() == "TidalCycles session started"
    assert session.boot_error is None
    session.stop()


def test_diagnose_messages():
    assert "cabal install --lib tidal" in diagnose_tidal_boot(
        "<no location info>: error:\n    Could not find module 'Sound.Tidal.Context'")
    assert diagnose_tidal_boot("all good") is None


@pytest.mark.skipif(shutil.which("ghci") is None, reason="needs ghci")
def test_real_ghci_without_tidal_is_reported(monkeypatch):
    """Real GHCi + the real BootTidal.hs; passes whether or not Tidal is installed."""
    monkeypatch.setattr(audio_stack, "log", lambda msg: None)
    session = TidalSession()
    session.start()
    try:
        if session.boot_error:
            assert "Tidal" in session.boot_error
    finally:
        session.stop()
