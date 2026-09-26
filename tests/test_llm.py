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


def test_theme_picks_style_unless_style_given(monkeypatch, capsys):
    import random
    import sys

    import orchestrator

    monkeypatch.delenv("YS_STYLE", raising=False)
    monkeypatch.setattr(sys, "argv", ["orchestrator.py", "--llm", "none", "--theme", "frankfurt deep house"])
    args = orchestrator.parse_args()
    director = orchestrator.build_director(args, random.Random(0))
    assert director.style == "deephouse"
    monkeypatch.setattr(sys, "argv", ["orchestrator.py", "--llm", "none", "--style", "lofi",
                                      "--theme", "frankfurt deep house"])
    args = orchestrator.parse_args()
    assert orchestrator.build_director(args, random.Random(0)).style == "lofi"


def test_converse_logs_both_sides_and_records_the_call(monkeypatch):
    import json

    import config
    from music import llm

    monkeypatch.setattr(llm, "complete", lambda *a, **k: 'Sure!\n```json\n{"op": "mute", "role": "beat"}\n```')
    lines = []
    reply = llm.converse("Performer", "local/test-model", "sys", "the prompt", log=lines.append, asks="next edit")
    assert "mute" in reply
    assert lines[0] == "💬 Performer → local/test-model: next edit (10 chars)"
    assert lines[1].startswith("💬 Performer ← local/test-model (")
    assert lines[1].endswith('{"op": "mute", "role": "beat"}')
    with open(f"{config.RUNTIME_DIR}/llm_calls.jsonl") as f:
        record = json.loads(f.readlines()[-1])
    assert record["agent"] == "Performer" and record["prompt"] == "the prompt" and "mute" in record["reply"]


def test_converse_logs_failures(monkeypatch):
    import pytest

    from music import llm

    def boom(*a, **k):
        raise llm.LLMError("HTTP 503 from server")

    monkeypatch.setattr(llm, "complete", boom)
    lines = []
    with pytest.raises(llm.LLMError):
        llm.converse("Director", "local/m", "sys", "p", log=lines.append)
    assert "✗ HTTP 503 from server" in lines[-1]


def test_claude_request_shape_per_model(monkeypatch):
    import types

    from music import llm

    calls = []
    reply = types.SimpleNamespace(stop_reason="end_turn",
                                  content=[types.SimpleNamespace(type="text", text='{"op": "mute"}')])

    def create(**kw):
        calls.append(kw)
        return reply

    client = types.SimpleNamespace(messages=types.SimpleNamespace(create=create),
                                   beta=types.SimpleNamespace(messages=types.SimpleNamespace(create=create)))
    monkeypatch.setattr(llm, "_claude_client", types.SimpleNamespace(with_options=lambda **k: client))
    assert llm._claude("claude-haiku-4-5", "sys", "hi", 30, 500) == '{"op": "mute"}'
    assert "fallbacks" not in calls[-1] and "betas" not in calls[-1]
    llm._claude("claude-opus-5", "sys", "hi", 30, 500)
    assert calls[-1]["fallbacks"] == "default"
