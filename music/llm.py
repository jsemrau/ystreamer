"""
LLM client for the Director, with three selectable providers:

- claude      : Anthropic's Claude via the official `anthropic` SDK
                (needs ANTHROPIC_API_KEY or an `ant auth login` profile)
- local       : a local llama.cpp server (OpenAI-compatible, LOCAL_LLM_API_BASE),
                called with plain HTTP so it needs no extra packages
- jevtypesafe : Jev (jevtypesafeai.com), a typed-decision API. It answers
                choice/score/noul questions instead of writing text, so the
                Director calls `decide()` for it rather than `complete()`
                (needs JEV_KEY)

Model ids are "<provider>/<model>", e.g. "claude/claude-opus-5",
"local/qwen-agentworld-35b", "jevtypesafe/jev-latest". A bare provider name
("claude", "local", "jevtypesafe") uses that provider's default model. Any
other id is passed to LiteLLM unchanged ("gpt-5-mini", "ollama/...").

Every call has a hard timeout and raises LLMError on any failure; the
Director then falls back to its deterministic plan.
"""

from __future__ import annotations

import concurrent.futures
import json
import os
import re

import config

_executor = concurrent.futures.ThreadPoolExecutor(max_workers=2, thread_name_prefix="llm")

PROVIDERS = ("claude", "local", "jevtypesafe")
DEFAULT_MODELS = {
    "claude": os.getenv("YS_CLAUDE_MODEL", "claude-opus-5"),
    "local": os.getenv("YS_LOCAL_MODEL", "qwen-agentworld-35b"),
    "jevtypesafe": os.getenv("YS_JEV_MODEL", "jev-latest"),
}
# Jev's hosted endpoint (prepaid JEV_KEY). With a TypeSafe key, set
# YS_JEV_API_BASE=https://api.typesafe.ai/v1/systemone instead.
JEV_API_BASE = "https://jevtypesafeai.com/api/v1/decide"


class LLMError(RuntimeError):
    pass


def resolve(model_id: str) -> tuple[str, str]:
    """Split a model id into (provider, model). Provider "litellm" = passthrough."""
    model_id = (model_id or "").strip()
    if model_id in PROVIDERS:
        return model_id, DEFAULT_MODELS[model_id]
    prefix, _, rest = model_id.partition("/")
    if prefix in ("claude", "anthropic") and rest:
        return "claude", rest
    if prefix in ("local", "jevtypesafe") and rest:
        return prefix, rest
    if model_id.startswith("claude-"):
        return "claude", model_id
    return "litellm", model_id


# ---------------------------------------------------------------------------
# Providers
# ---------------------------------------------------------------------------

_claude_client = None


def _claude(model: str, system: str, user: str, timeout: float, max_tokens: int) -> str:
    import anthropic

    global _claude_client
    if _claude_client is None:
        # Credentials resolve from ANTHROPIC_API_KEY / ANTHROPIC_AUTH_TOKEN / `ant auth login`.
        _claude_client = anthropic.Anthropic(max_retries=2)

    kwargs = {}
    effort = os.getenv("YS_CLAUDE_EFFORT")  # low | medium | high (API default) | xhigh | max
    if effort:
        kwargs["output_config"] = {"effort": effort}
    try:
        response = _claude_client.with_options(timeout=timeout).beta.messages.create(
            model=model,
            max_tokens=max(max_tokens, 16000),
            system=system,
            messages=[{"role": "user", "content": user}],
            # If a request is declined by a safety classifier, let the API
            # retry it on a suitable fallback model within the same call.
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            **kwargs,
        )
    except anthropic.APIStatusError as e:
        raise LLMError(f"Claude API error {e.status_code}: {e.message}") from e
    except anthropic.APIConnectionError as e:
        raise LLMError(f"Claude connection error: {e}") from e
    if response.stop_reason == "refusal":
        raise LLMError("Claude declined the request")
    return "".join(block.text for block in response.content if block.type == "text")


def _openai_compatible(model: str, api_base: str, api_key: str, system: str, user: str, timeout: float,
                       max_tokens: int, temperature: float, extra_body: dict | None = None) -> str:
    """POST to an OpenAI-compatible /chat/completions endpoint (llama.cpp, vLLM, ...)."""
    import requests

    url = api_base.rstrip("/")
    if not url.endswith("/chat/completions"):
        url += "/chat/completions"
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    body = {
        "model": model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "max_tokens": max_tokens,
        "temperature": temperature,
        **(extra_body or {}),
    }
    try:
        resp = requests.post(url, headers=headers, json=body, timeout=timeout)
    except requests.RequestException as e:
        raise LLMError(f"cannot reach {url}: {e}") from e
    if resp.status_code >= 400:
        raise LLMError(f"HTTP {resp.status_code} from {url}: {resp.text[:300]}")
    try:
        return resp.json()["choices"][0]["message"]["content"] or ""
    except (ValueError, KeyError, IndexError, TypeError) as e:
        raise LLMError(f"unexpected reply from {url}: {resp.text[:300]}") from e


def _jev(model: str, state, questions: dict, timeout: float) -> dict:
    """POST typed questions to Jev and return its checked `answers` map."""
    import requests

    api_key = os.getenv("JEV_KEY", "")
    if not api_key:
        raise LLMError("jevtypesafe provider selected but JEV_KEY is not set in .env")
    url = os.getenv("YS_JEV_API_BASE") or JEV_API_BASE
    try:
        resp = requests.post(url, headers={"Authorization": f"Bearer {api_key}"}, timeout=timeout,
                             json={"model": model, "state": state, "questions": questions})
    except requests.RequestException as e:
        raise LLMError(f"cannot reach {url}: {e}") from e
    if resp.status_code >= 400:
        raise LLMError(f"Jev HTTP {resp.status_code}: {resp.text[:300]}")
    try:
        answers = resp.json()["answers"]
    except (ValueError, KeyError, TypeError) as e:
        raise LLMError(f"unexpected reply from Jev: {resp.text[:300]}") from e
    # The answer field is named after the question type: {"type": "choice", "choice": ...}.
    for name, question in questions.items():
        kind = question["type"]
        answer = answers.get(name) if isinstance(answers, dict) else None
        value = answer.get(kind) if isinstance(answer, dict) and answer.get("type") == kind else None
        if kind == "choice":
            valid = isinstance(value, str) and value in question["criteria"]
        else:
            valid = isinstance(value, (int, float))
        if not valid:
            raise LLMError(f"Jev gave no valid {kind} answer for {name!r}: {str(answer)[:200]}")
    return answers


def _run(provider: str, call, timeout: float):
    """Run `call` with a hard timeout, turning every failure into LLMError."""
    future = _executor.submit(call)
    try:
        return future.result(timeout=timeout + 5)
    except concurrent.futures.TimeoutError as e:
        raise LLMError(f"{provider} call timed out after {timeout:.0f}s") from e
    except LLMError:
        raise
    except (KeyboardInterrupt, SystemExit):
        raise
    except BaseException as e:  # network, auth, bad model id, even native-extension panics
        raise LLMError(f"{provider} call failed: {type(e).__name__}: {e}") from e


def complete(model_id: str, system: str, user: str, timeout: float | None = None,
             max_tokens: int = 1200, temperature: float = 0.8) -> str:
    """Return the model's text reply, or raise LLMError (including on timeout)."""
    timeout = timeout or config.LLM_TIMEOUT_SECONDS
    provider, model = resolve(model_id)
    if provider == "jevtypesafe":
        raise LLMError("Jev answers typed questions, not free text; use decide()")

    def call() -> str:
        if provider == "claude":
            return _claude(model, system, user, timeout, max_tokens)
        if provider == "local":
            # Qwen-style chat templates otherwise "think" for thousands of tokens.
            return _openai_compatible(model, config.LOCAL_LLM_API_BASE, "", system, user, timeout, max_tokens,
                                      temperature, {"chat_template_kwargs": {"enable_thinking": False}})
        import litellm

        response = litellm.completion(
            model=model, max_tokens=max_tokens, temperature=temperature, timeout=timeout,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        )
        return response.choices[0].message.content or ""

    return _run(provider, call, timeout)


def decide(model_id: str, state, questions: dict, timeout: float | None = None) -> dict:
    """Ask Jev typed questions and return {name: answer}, or raise LLMError.

    `state` is the context (string, JSON object or list of strings). Each
    question is {"type": "choice" | "score" | "noul", "instructions": str,
    "criteria": {option: description} (choice) | [levels] (score)}. Answers
    are checked against their questions, so a choice is always one of its
    criteria keys.
    """
    timeout = timeout or config.LLM_TIMEOUT_SECONDS
    provider, model = resolve(model_id)
    if provider != "jevtypesafe":
        raise LLMError(f"{provider} does not answer typed questions")
    return _run(provider, lambda: _jev(model, state, questions, timeout), timeout)


_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


def extract_json(text: str) -> dict:
    """Pull the first JSON object out of a model reply (fenced or bare)."""
    if not text:
        raise LLMError("empty reply")
    candidates = _FENCE_RE.findall(text) + [text]
    for candidate in candidates:
        start = candidate.find("{")
        while start != -1:
            depth = 0
            in_str = False
            escape = False
            for i in range(start, len(candidate)):
                ch = candidate[i]
                if in_str:
                    if escape:
                        escape = False
                    elif ch == "\\":
                        escape = True
                    elif ch == '"':
                        in_str = False
                    continue
                if ch == '"':
                    in_str = True
                elif ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        try:
                            value = json.loads(candidate[start:i + 1])
                        except json.JSONDecodeError:
                            break
                        if isinstance(value, dict):
                            return value
                        break
            start = candidate.find("{", start + 1)
    raise LLMError("no JSON object found in reply")
