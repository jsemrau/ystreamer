"""Keep ystreamer on the project's own virtualenv (./venv).

Entry points call ensure_venv() before importing any third-party package.
Started with another interpreter (system python3, ~/.local tools, ...), the
process re-executes itself with ./venv/bin/python and the same arguments, so
`python3 orchestrator.py` and `python3 -m eval.analyze` both end up in the venv.
Only the standard library may be imported here.
"""

import os
import sys
from pathlib import Path

VENV_DIR = Path(__file__).resolve().parent / "venv"
VENV_PYTHON = VENV_DIR / "bin" / "python"
_REEXEC_FLAG = "YS_VENV_REEXEC"


def in_venv() -> bool:
    return Path(sys.prefix).resolve() == VENV_DIR.resolve()


def ensure_venv() -> None:
    """Re-run the current command with ./venv/bin/python unless already on it."""
    if in_venv():
        os.environ.pop(_REEXEC_FLAG, None)  # don't leak into child processes
        return
    if not VENV_PYTHON.exists():
        sys.exit(f"ystreamer only runs in {VENV_DIR}, which doesn't exist yet. Run ./install.sh first.")
    if os.environ.get(_REEXEC_FLAG):  # re-executed once already and still outside: don't loop
        sys.exit(f"ystreamer only runs in {VENV_DIR}, but {VENV_PYTHON} didn't start inside it.")
    os.environ[_REEXEC_FLAG] = "1"
    os.execv(VENV_PYTHON, [str(VENV_PYTHON), *sys.orig_argv[1:]])
