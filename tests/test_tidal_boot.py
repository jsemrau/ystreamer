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


def test_local_llm_status_and_wait(monkeypatch):
    import types

    import requests

    import config

    responses = iter([503, 503, 200])

    def fake_get(url, timeout):
        return types.SimpleNamespace(status_code=next(responses))

    logs = []
    monkeypatch.setattr(requests, "get", fake_get)
    monkeypatch.setattr(audio_stack, "log", logs.append)
    monkeypatch.setattr(audio_stack.time, "sleep", lambda s: None)
    monkeypatch.setattr(config, "LOCAL_LLM_API_BASE", "http://127.0.0.1:8080/v1")
    monkeypatch.setattr(audio_stack, "llama_load_progress", lambda since: (None, ""))
    assert audio_stack.wait_for_local_llm(timeout=30)
    assert logs[0] == "[llama-server] loading the model, waiting..."
    assert logs[-1].startswith("[llama-server] ready after ")


def test_local_llm_status_down(monkeypatch):
    import requests

    def refuse(url, timeout):
        raise requests.ConnectionError("refused")

    monkeypatch.setattr(requests, "get", refuse)
    assert audio_stack.local_llm_status() == "down"


def test_wait_for_local_llm_shows_progress(monkeypatch):
    import config

    statuses = iter(["loading", "loading", "loading", "ready"])
    progress = iter([(5, "5% (0.8/16.5 GB) · loading weights"), (7, "7% (1.2/16.5 GB) · loading weights"),
                     (100, "100% (16.5/16.5 GB) · warming up")])
    logs = []
    monkeypatch.setattr(audio_stack, "local_llm_status", lambda: next(statuses))
    monkeypatch.setattr(audio_stack, "llama_load_progress", lambda since: next(progress))
    monkeypatch.setattr(audio_stack, "log", logs.append)
    monkeypatch.setattr(audio_stack.time, "sleep", lambda s: None)
    monkeypatch.setattr(config, "LLAMA_SERVER_LOG", "/nonexistent/llama.log")
    assert audio_stack.wait_for_local_llm(timeout=30)
    shown = [line for line in logs if "%" in line]
    assert len(shown) == 2          # 7% is in the same 10% band and stage as 5%
    assert "5% (0.8/16.5 GB) · loading weights · 0:00" in shown[0]
    assert "warming up" in shown[1]


def test_llama_load_progress_from_proc_and_log(monkeypatch, tmp_path):
    import os
    import time as _time

    import config

    model = tmp_path / "m.gguf"
    model.write_bytes(b"x" * 1000)
    log_file = tmp_path / "llama.log"
    log_file.write_text("llama_model_loader: loaded meta data\nload_tensors: offloaded 65/65 layers to GPU\n")
    monkeypatch.setattr(config, "LLAMA_GGUF_MODEL", str(model))
    monkeypatch.setattr(config, "LLAMA_SERVER_LOG", str(log_file))
    monkeypatch.setattr(audio_stack, "_llama_pid", lambda: os.getpid())
    monkeypatch.setattr(audio_stack, "_llama_bytes_loaded", lambda pid: 430)
    pct, text = audio_stack.llama_load_progress(_time.time() - 60)
    assert pct == 43
    assert text == "43% (0.0/0.0 GB) · offloaded 65/65 layers to GPU"
    assert audio_stack._llama_bytes_loaded(os.getpid()) is not None   # real /proc read works


def test_wait_for_local_llm_reports_crash(monkeypatch, tmp_path):
    import types

    import config

    log_file = tmp_path / "llama.log"
    log_file.write_text("load_tensors: loading model\n"
                        "ggml_backend_cuda_buffer_type_alloc_buffer: allocating 9000 MiB on device 0: "
                        "cudaMalloc failed: out of memory\n")
    statuses = iter(["loading", "down"])
    logs = []
    monkeypatch.setattr(config, "LLAMA_SERVER_LOG", str(log_file))
    monkeypatch.setattr(audio_stack, "local_llm_status", lambda: next(statuses))
    monkeypatch.setattr(audio_stack, "llama_load_progress", lambda since: (100, "100% (16.5/16.5 GB)"))
    monkeypatch.setattr(audio_stack, "_llama_process", types.SimpleNamespace(poll=lambda: None, returncode=None))
    monkeypatch.setattr(audio_stack, "log", logs.append)
    monkeypatch.setattr(audio_stack.time, "sleep", lambda s: None)
    assert not audio_stack.wait_for_local_llm(timeout=300)
    report = logs[-1]
    assert "stopped while loading" in report
    assert "cudaMalloc failed" in report
    assert "LLAMA_CTX_SIZE" in report


def test_launch_uses_config_and_detects_exit(monkeypatch, tmp_path):
    import config

    fake = tmp_path / "llama-server"
    fake.write_text("#!/bin/sh\necho \"$@\"\necho 'llama_model_load: error loading model: unknown model architecture: qwen9'\nexit 1\n")
    fake.chmod(0o755)
    model = tmp_path / "m.gguf"
    model.write_bytes(b"GGUF")
    logs = []
    monkeypatch.setattr(config, "LLAMA_SERVER_BIN", str(fake))
    monkeypatch.setattr(config, "LLAMA_GGUF_MODEL", str(model))
    monkeypatch.setattr(config, "LLAMA_SERVER_LOG", str(tmp_path / "logs" / "llama.log"))
    monkeypatch.setattr(config, "LLAMA_CTX_SIZE", 8192)
    monkeypatch.setattr(config, "LLAMA_SERVER_ARGS", "-fa on")
    monkeypatch.setattr(config, "LOCAL_LLM_API_BASE", "http://127.0.0.1:8099/v1")
    monkeypatch.setattr(audio_stack, "local_llm_status", lambda: "down")
    monkeypatch.setattr(audio_stack, "_llama_process", None)
    monkeypatch.setattr(audio_stack, "log", logs.append)
    audio_stack.ensure_local_llm_server(wait=False)
    audio_stack._llama_process.wait(timeout=10)
    assert not audio_stack.wait_for_local_llm(timeout=30)
    assert "--ctx-size 8192" in logs[1] and "--port 8099" in logs[1] and logs[1].endswith("-fa on")
    assert "exit code 1" in logs[-1]
    assert "update and rebuild llama.cpp" in logs[-1].lower()


def test_superdirt_port_held_by_another_program(monkeypatch):
    import socket

    holder = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        holder.bind(("127.0.0.1", audio_stack.SUPERDIRT_PORT))
    except OSError:
        return          # something on this machine already uses the port; nothing to test
    try:
        assert not audio_stack._superdirt_port_free()
        monkeypatch.setattr(audio_stack, "_stale_superdirt_pids", lambda: [])
        monkeypatch.setattr(audio_stack, "log", lambda *_: None)
        result = audio_stack.start_superdirt()
        assert result.startswith("Error: UDP port 57120 is in use")
        assert "pkill sclang" in result
    finally:
        holder.close()
    assert audio_stack._superdirt_port_free()
