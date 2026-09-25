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


ROUTE = {"route": {"type": "choice", "instructions": "Where?", "criteria": {"a": "A", "b": "B"}}}


def fake_jev(monkeypatch, status_code=200, answers=None, seen=None):
    import requests

    def fake_post(url, headers, timeout, json):
        if seen is not None:
            seen.update(url=url, headers=headers, body=json)
        return types.SimpleNamespace(status_code=status_code, text='{"error": "Insufficient credits"}',
                                     json=lambda: {"model": "jev-1.13.0", "answers": answers or {}})

    monkeypatch.setenv("JEV_KEY", "k")
    monkeypatch.delenv("YS_JEV_API_BASE", raising=False)
    monkeypatch.setattr(requests, "post", fake_post)


def test_jev_requires_key(monkeypatch):
    monkeypatch.delenv("JEV_KEY", raising=False)
    with pytest.raises(llm.LLMError, match="JEV_KEY"):
        llm.decide("jevtypesafe", "state", ROUTE)


def test_jev_decide_sends_typed_questions(monkeypatch):
    seen = {}
    questions = {**ROUTE, "escalate": {"type": "noul", "instructions": "Escalate?"}}
    fake_jev(monkeypatch, answers={"route": {"type": "choice", "choice": "b", "confidence": 0.9},
                                   "escalate": {"type": "noul", "noul": 0.2}}, seen=seen)
    answers = llm.decide("jevtypesafe/jev-latest", {"ctx": 1}, questions)
    assert answers["route"]["choice"] == "b" and answers["escalate"]["noul"] == 0.2
    assert seen["url"] == llm.JEV_API_BASE
    assert seen["headers"]["Authorization"] == "Bearer k"
    assert seen["body"] == {"model": "jev-latest", "state": {"ctx": 1}, "questions": questions}


@pytest.mark.parametrize("answer", [
    {"type": "choice", "choice": "c"},   # not one of the criteria
    {"type": "noul", "noul": 0.5},       # wrong type
    None,                                # missing
])
def test_jev_rejects_invalid_answers(monkeypatch, answer):
    fake_jev(monkeypatch, answers={"route": answer} if answer else {})
    with pytest.raises(llm.LLMError, match="route"):
        llm.decide("jevtypesafe", "state", ROUTE)


def test_jev_http_error(monkeypatch):
    fake_jev(monkeypatch, status_code=402)
    with pytest.raises(llm.LLMError, match="402.*Insufficient credits"):
        llm.decide("jevtypesafe", "state", ROUTE)


def test_jev_is_not_a_text_llm():
    with pytest.raises(llm.LLMError, match="typed questions"):
        llm.complete("jevtypesafe", "sys", "user")


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
