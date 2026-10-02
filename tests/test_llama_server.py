"""The engine reuses a running llama-server only if it serves the model .env asks for."""

import audio_stack
import config


def _patch(monkeypatch, loaded, stops):
    statuses = iter(["ready", "down"])
    launched = []
    monkeypatch.setattr(audio_stack, "local_llm_status", lambda: next(statuses, "down"))
    monkeypatch.setattr(audio_stack, "serving_model", lambda: loaded)
    monkeypatch.setattr(audio_stack, "_stop_stale_llama", lambda port: stops)
    monkeypatch.setattr(audio_stack, "log", lambda *a: None)
    monkeypatch.setattr(config, "LLAMA_GGUF_MODEL", "/models/wanted.gguf")
    monkeypatch.setattr(audio_stack.os.path, "exists", lambda p: False)   # stop before the real launch
    return launched


def test_a_server_with_another_model_is_replaced(monkeypatch):
    logs = []
    _patch(monkeypatch, "/models/old.gguf", stops=True)
    monkeypatch.setattr(audio_stack, "log", logs.append)
    audio_stack.ensure_local_llm_server(wait=False)
    assert any("stopped it, starting the right one" in m for m in logs)
    assert not any("already running" in m for m in logs)


def test_a_server_with_the_right_model_is_reused(monkeypatch):
    logs = []
    _patch(monkeypatch, "/models/wanted.gguf", stops=False)
    monkeypatch.setattr(audio_stack, "log", logs.append)
    audio_stack.ensure_local_llm_server(wait=False)
    assert any("already running" in m and "wanted.gguf" in m for m in logs)


def test_someone_elses_server_is_left_alone_with_a_warning(monkeypatch):
    logs = []
    _patch(monkeypatch, "/models/old.gguf", stops=False)
    monkeypatch.setattr(audio_stack, "log", logs.append)
    audio_stack.ensure_local_llm_server(wait=False)
    assert any("WARNING" in m and "old.gguf" in m for m in logs)
