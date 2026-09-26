"""
LLM client for the Director, with three selectable providers:

- claude      : Anthropic's Claude via the official `anthropic` SDK
                (needs ANTHROPIC_API_KEY or an `ant auth login` profile)
- local       : a local llama.cpp server (OpenAI-compatible, LOCAL_LLM_API_BASE),
                called with plain HTTP so it needs no extra packages
- jevtypesafe : jevtypesafeai.com (endpoint/key/style configured in .env)

Model ids are "<provider>/<model>", e.g. "claude/claude-opus-5",
"local/qwen-agentworld-35b", "jevtypesafe/<model>". A bare provider name
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
    "claude": os.getenv("YS_CLAUDE_MODEL") or os.getenv("ANTHROPIC_MODEL") or "claude-opus-5",
    "local": os.getenv("YS_LOCAL_MODEL", "qwen-agentworld-35b"),
    "jevtypesafe": os.getenv("YS_JEV_MODEL", "default"),
}


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


# Models whose safety classifiers can decline a request; they get the
# server-side refusal fallback.
_FALLBACK_MODELS = ("claude-opus-5", "claude-fable-5", "claude-mythos-5")


def _claude(model: str, system: str, user: str, timeout: float, max_tokens: int) -> str:
    import anthropic

    global _claude_client
    if _claude_client is None:
        # Credentials resolve from ANTHROPIC_API_KEY / ANTHROPIC_AUTH_TOKEN / `ant auth login`.
        try:
            _claude_client = anthropic.Anthropic(max_retries=2)
        except Exception as e:
            raise LLMError(f"no Claude credentials ({e})") from e

    kwargs = {}
    effort = os.getenv("YS_CLAUDE_EFFORT")  # low | medium | high (API default) | xhigh | max
    if effort:
        kwargs["output_config"] = {"effort": effort}
    client = _claude_client.with_options(timeout=timeout)
    request = dict(model=model, max_tokens=max(max_tokens, 16000), system=system,
                   messages=[{"role": "user", "content": user}], **kwargs)
    try:
        if model.startswith(_FALLBACK_MODELS):
            # If a request is declined by a safety classifier, let the API
            # retry it on a suitable fallback model within the same call.
            response = client.beta.messages.create(**request, betas=["server-side-fallback-2026-07-01"],
                                                   fallbacks="default")
        else:  # Sonnet, Haiku: a plain request
            response = client.messages.create(**request)
    except anthropic.APIStatusError as e:
        raise LLMError(f"Claude API error {e.status_code}: {e.message}") from e
    except anthropic.APIConnectionError as e:
        raise LLMError(f"Claude connection error: {e}") from e
    except TypeError as e:
        if "authentication" in str(e):
            raise LLMError("no Claude API key: set ANTHROPIC_API_KEY=sk-ant-... in .env "
                           "(one line, no quotes or spaces)") from e
        raise
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
    except requests.ConnectionError as e:
        raise LLMError(f"cannot reach {url} (no server running there? for the local model set "
                       f"LLAMA_SERVER_BIN and LLAMA_GGUF_MODEL in .env)") from e
    except requests.RequestException as e:
        raise LLMError(f"cannot reach {url}: {e}") from e
    if resp.status_code >= 400:
        raise LLMError(f"HTTP {resp.status_code} from {url}: {resp.text[:300]}")
    try:
        return resp.json()["choices"][0]["message"]["content"] or ""
    except (ValueError, KeyError, IndexError, TypeError) as e:
        raise LLMError(f"unexpected reply from {url}: {resp.text[:300]}") from e


def _jevtypesafe(model: str, system: str, user: str, timeout: float, max_tokens: int, temperature: float) -> str:
    """jevtypesafeai.com. Configure in .env:

    YS_JEV_API_BASE  base URL of its API
    YS_JEV_API_KEY   API key
    YS_JEV_API_STYLE "openai" (OpenAI-compatible /chat/completions, default)
                     or "json" (POST {"system","prompt","model"} -> {"text"|"output"|"response"})
    """
    api_base = os.getenv("YS_JEV_API_BASE", "")
    api_key = os.getenv("YS_JEV_API_KEY", "")
    if not api_base:
        raise LLMError("jevtypesafe provider selected but YS_JEV_API_BASE is not set in .env")
    style = os.getenv("YS_JEV_API_STYLE", "openai").lower()
    if style == "openai":
        return _openai_compatible(model, api_base, api_key, system, user, timeout, max_tokens, temperature)

    import requests

    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    resp = requests.post(api_base, headers=headers, timeout=timeout,
                         json={"model": model, "system": system, "prompt": user, "max_tokens": max_tokens})
    if resp.status_code >= 400:
        raise LLMError(f"jevtypesafe HTTP {resp.status_code}: {resp.text[:300]}")
    data = resp.json()
    for key in ("text", "output", "response", "content"):
        if isinstance(data.get(key), str):
            return data[key]
    return json.dumps(data)  # structured reply: let extract_json find the object


def complete(model_id: str, system: str, user: str, timeout: float | None = None,
             max_tokens: int = 1200, temperature: float = 0.8) -> str:
    """Return the model's text reply, or raise LLMError (including on timeout)."""
    timeout = timeout or config.LLM_TIMEOUT_SECONDS
    provider, model = resolve(model_id)

    def call() -> str:
        if provider == "claude":
            return _claude(model, system, user, timeout, max_tokens)
        if provider == "local":
            # Qwen-style chat templates otherwise "think" for thousands of tokens.
            return _openai_compatible(model, config.LOCAL_LLM_API_BASE, "", system, user, timeout, max_tokens,
                                      temperature, {"chat_template_kwargs": {"enable_thinking": False}})
        if provider == "jevtypesafe":
            return _jevtypesafe(model, system, user, timeout, max_tokens, temperature)
        import litellm

        response = litellm.completion(
            model=model, max_tokens=max_tokens, temperature=temperature, timeout=timeout,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        )
        return response.choices[0].message.content or ""

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


def _one_line(text: str, limit: int = 400) -> str:
    """The reply's JSON (or text) on one line, for the terminal."""
    text = text or ""
    candidates = _FENCE_RE.findall(text)
    body = candidates[0] if candidates else text
    start, end = body.find("{"), body.rfind("}")
    if start != -1 and end > start:
        body = body[start:end + 1]
    body = " ".join(body.split())
    return body if len(body) <= limit else body[:limit - 1] + "…"


def converse(agent: str, model_id: str, system: str, user: str, *, log=print, asks: str = "",
             show_full: bool = False, max_tokens: int = 1200, temperature: float = 0.8) -> str:
    """complete() plus a visible record of the exchange: what the agent asked
    which model, what came back and how long it took (terminal), and the full
    prompt and reply (runtime/llm_calls.jsonl). Raises LLMError like complete()."""
    import time

    from . import runtime

    provider, model = resolve(model_id)
    who = f"{provider}/{model}"
    log(f"💬 {agent} → {who}: {asks or 'request'} ({len(user):,} chars)")
    if show_full:
        log(f"   ┌─ prompt to {who}\n" + "\n".join(f"   │ {line}" for line in user.splitlines()) + "\n   └─")
    started = time.monotonic()
    record = {"agent": agent, "model": who, "asks": asks, "system": system, "prompt": user}
    try:
        reply = complete(model_id, system, user, max_tokens=max_tokens, temperature=temperature)
    except LLMError as e:
        seconds = time.monotonic() - started
        log(f"💬 {agent} ← {who} ({seconds:.1f}s): ✗ {e}")
        runtime.append_llm_call({**record, "seconds": round(seconds, 2), "error": str(e)})
        raise
    seconds = time.monotonic() - started
    log(f"💬 {agent} ← {who} ({seconds:.1f}s): {_one_line(reply)}")
    if show_full:
        log("   ┌─ raw reply\n" + "\n".join(f"   │ {line}" for line in reply.strip().splitlines()) + "\n   └─")
    runtime.append_llm_call({**record, "seconds": round(seconds, 2), "reply": reply})
    return reply
