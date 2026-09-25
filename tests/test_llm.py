import argparse
import types

import pytest

from music import llm


@pytest.mark.parametrize("model_id,expected", [
    ("claude", ("claude", llm.DEFAULT_MODELS["claude"])),
    ("claude/claude-sonnet-5", ("claude", "claude-sonnet-5")),
    ("anthropic/claude-opus-5", ("claude", "claude-opus-5")),
    ("claude-opus-5", ("claude", "claude-opus-5")),
    ("local", ("local", llm.DEFAULT_MODELS["local"])),
    ("local/qwen-agentworld-35b", ("local", "qwen-agentworld-35b")),
    ("jevtypesafe/foo", ("jevtypesafe", "foo")),
    ("gpt-5-mini", ("litellm", "gpt-5-mini")),
])
def test_resolve(model_id, expected):
    assert llm.resolve(model_id) == expected


class FakeMessages:
    def __init__(self, reply, stop_reason="end_turn"):
        self.reply, self.stop_reason, self.calls = reply, stop_reason, []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return types.SimpleNamespace(stop_reason=self.stop_reason,
                                     content=[types.SimpleNamespace(type="text", text=self.reply)])


class FakeClaude:
    def __init__(self, messages):
        self.beta = types.SimpleNamespace(messages=messages)

    def with_options(self, **kwargs):
        return self


def test_claude_uses_sdk_with_fallbacks(monkeypatch):
    messages = FakeMessages('{"title": "x"}')
    monkeypatch.setattr(llm, "_claude_client", FakeClaude(messages))
    monkeypatch.setenv("YS_CLAUDE_EFFORT", "medium")
    assert llm.extract_json(llm.complete("claude", "sys", "user")) == {"title": "x"}
    call = messages.calls[0]
    assert call["model"] == llm.DEFAULT_MODELS["claude"]
    assert call["fallbacks"] == "default"
    assert call["betas"] == ["server-side-fallback-2026-07-01"]
    assert call["output_config"] == {"effort": "medium"}
    assert "temperature" not in call  # rejected by current Claude models


def test_claude_refusal_raises(monkeypatch):
    monkeypatch.setattr(llm, "_claude_client", FakeClaude(FakeMessages("", stop_reason="refusal")))
    with pytest.raises(llm.LLMError):
        llm.complete("claude", "sys", "user")


def test_jevtypesafe_requires_config(monkeypatch):
    monkeypatch.delenv("YS_JEV_API_BASE", raising=False)
    with pytest.raises(llm.LLMError, match="YS_JEV_API_BASE"):
        llm.complete("jevtypesafe/m", "sys", "user")


def test_jevtypesafe_json_style(monkeypatch):
    import requests

    seen = {}

    def fake_post(url, headers, timeout, json):
        seen.update(url=url, headers=headers, body=json)
        return types.SimpleNamespace(status_code=200, json=lambda: {"text": '{"ok": 1}'}, text="")

    monkeypatch.setenv("YS_JEV_API_BASE", "https://example.test/api")
    monkeypatch.setenv("YS_JEV_API_KEY", "k")
    monkeypatch.setenv("YS_JEV_API_STYLE", "json")
    monkeypatch.setattr(requests, "post", fake_post)
    assert llm.complete("jevtypesafe/m", "sys", "user") == '{"ok": 1}'
    assert seen["headers"]["Authorization"] == "Bearer k"
    assert seen["body"]["model"] == "m" and seen["body"]["prompt"] == "user"


def test_orchestrator_model_selection():
    from orchestrator import director_model_id

    ns = lambda **kw: argparse.Namespace(**{"director_model": "", "llm": "none", "model": "", **kw})
    assert director_model_id(ns()) is None
    assert director_model_id(ns(llm="claude")) == "claude"
    assert director_model_id(ns(llm="claude", model="claude-sonnet-5")) == "claude/claude-sonnet-5"
    assert director_model_id(ns(llm="local", director_model="gpt-5-mini")) == "gpt-5-mini"
