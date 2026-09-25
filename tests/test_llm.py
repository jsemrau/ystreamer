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


def test_local_is_the_default_llm(monkeypatch):
    import sys

    import orchestrator

    monkeypatch.delenv("YS_LLM", raising=False)
    monkeypatch.setattr(sys, "argv", ["orchestrator.py"])
    args = orchestrator.parse_args()
    assert args.llm == "local"
    assert orchestrator.director_model_id(args) == "local"
    assert llm.resolve("local")[0] == "local"


def test_local_uses_openai_compatible_http(monkeypatch):
    import requests

    seen = {}

    def fake_post(url, headers, json, timeout):
        seen.update(url=url, body=json)
        return types.SimpleNamespace(status_code=200, text="",
                                     json=lambda: {"choices": [{"message": {"content": '{"a": 1}'}}]})

    monkeypatch.setattr(requests, "post", fake_post)
    assert llm.complete("local", "sys", "user") == '{"a": 1}'
    assert seen["url"].endswith("/v1/chat/completions")
    assert seen["body"]["model"] == llm.DEFAULT_MODELS["local"]
    assert seen["body"]["chat_template_kwargs"] == {"enable_thinking": False}


def test_unreachable_local_server_is_an_llm_error(monkeypatch):
    import requests

    def refuse(*a, **k):
        raise requests.ConnectionError("refused")

    monkeypatch.setattr(requests, "post", refuse)
    with pytest.raises(llm.LLMError, match="cannot reach"):
        llm.complete("local", "sys", "user")


def test_non_exception_errors_become_llm_errors(monkeypatch):
    class Panic(BaseException):
        pass

    def boom(*a, **k):
        raise Panic("native crash")

    monkeypatch.setattr(llm, "_openai_compatible", boom)
    with pytest.raises(llm.LLMError, match="Panic"):
        llm.complete("local", "sys", "user")


def test_style_and_theme_from_env(monkeypatch):
    import sys

    import orchestrator

    monkeypatch.setenv("YS_STYLE", "ambient")
    monkeypatch.setenv("YS_THEME", "slow sunrise over the ocean, hopeful")
    monkeypatch.setattr(sys, "argv", ["orchestrator.py"])
    args = orchestrator.parse_args()
    assert args.style == "ambient"
    assert args.theme == "slow sunrise over the ocean, hopeful"

    monkeypatch.setattr(sys, "argv", ["orchestrator.py", "--style", "lofi", "--theme", "rain"])
    args = orchestrator.parse_args()
    assert (args.style, args.theme) == ("lofi", "rain")

    monkeypatch.setenv("YS_STYLE", "polka")
    monkeypatch.setattr(sys, "argv", ["orchestrator.py"])
    with pytest.raises(SystemExit):
        orchestrator.parse_args()
