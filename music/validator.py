"""
Real type-checking of Tidal expressions with GHCi.

Runs a separate GHCi (no audio, no Tidal stream) with Sound.Tidal.Context
loaded and asks `:t (expr :: ControlPattern)`. That catches every error the
live session would reject, before anything is sent to the stream.

If GHCi or the Tidal library isn't installed, `available` is False and
`check` returns None ("unknown"), so callers can decide what to do.
"""

from __future__ import annotations

import subprocess
import threading
from queue import Empty, Queue


class TidalTypeChecker:
    def __init__(self, timeout: float = 30.0):
        self.timeout = timeout
        self.available = False
        self._proc: subprocess.Popen | None = None
        self._out: Queue[str] = Queue()
        self._lock = threading.Lock()
        self._counter = 0
        self._start()

    def _start(self) -> None:
        try:
            self._proc = subprocess.Popen(
                ["ghci", "-v0", "-XOverloadedStrings"],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1,
            )
        except FileNotFoundError:
            return
        proc = self._proc

        def pump():
            assert proc.stdout is not None
            for line in proc.stdout:
                self._out.put(line.rstrip("\n"))

        threading.Thread(target=pump, daemon=True).start()
        output = self._run(["import Sound.Tidal.Context"], timeout=120)
        self.available = output is not None and "error" not in output.lower() and "could not find" not in output.lower()

    def _run(self, lines: list[str], timeout: float | None = None) -> str | None:
        if self._proc is None or self._proc.poll() is not None:
            return None
        with self._lock:
            self._counter += 1
            sentinel = f"__YS_DONE_{self._counter}__"
            assert self._proc.stdin is not None
            for line in lines:
                self._proc.stdin.write(line + "\n")
            self._proc.stdin.write(f'putStrLn "{sentinel}"\n')
            self._proc.stdin.flush()
            collected = []
            while True:
                try:
                    line = self._out.get(timeout=timeout or self.timeout)
                except Empty:
                    return None
                if sentinel in line:
                    return "\n".join(collected)
                collected.append(line)

    def check(self, expr: str) -> str | None:
        """Return "" if expr type-checks as a ControlPattern, the error text if
        not, or None if checking isn't possible."""
        if not self.available:
            return None
        output = self._run([f":t (({expr}) :: ControlPattern)"])
        if output is None:
            return None
        if "error" in output.lower():
            return output.strip()[-800:]
        return ""

    def close(self) -> None:
        if self._proc and self._proc.poll() is None:
            try:
                assert self._proc.stdin is not None
                self._proc.stdin.write(":quit\n")
                self._proc.stdin.flush()
                self._proc.wait(timeout=5)
            except Exception:
                self._proc.kill()
