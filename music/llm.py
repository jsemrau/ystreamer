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
    except requests.Timeout as e:
        raise LLMError(f"timed out: no reply from {url} within {timeout:.0f}s. The model is too slow for this: "
                       "check the speed shown on its replies (tok/s), whether it is thinking (a local Qwen "
                       "should reply without <think>), whether all layers are on the GPU (LLAMA_GPU_LAYERS, "
                       "nvidia-smi), and logs/llama-server.log") from e
    except requests.ConnectionError as e:
        raise LLMError(f"cannot reach {url} (no server running there? for the local model set "
                       f"LLAMA_SERVER_BIN and LLAMA_GGUF_MODEL in .env)") from e
    except requests.RequestException as e:
        raise LLMError(f"cannot reach {url}: {e}") from e
    if resp.status_code >= 400:
        if "exceed_context_size" in resp.text or "exceeds the available context" in resp.text:
            raise LLMError(f"HTTP {resp.status_code}: the prompt doesn't fit the model's context window "
                           f"({resp.text[:160]}). Raise LLAMA_CTX_SIZE in .env: it is shared by 2 parallel "
                           "slots, so 16384 gives each request 8192 tokens (add LLAMA_SERVER_ARGS=-fa on "
                           "--cache-type-k q8_0 --cache-type-v q8_0 if memory is tight)")
        raise LLMError(f"HTTP {resp.status_code} from {url}: {resp.text[:300]}")
    try:
        data = resp.json()
        message = data["choices"][0]["message"]
        text = message.get("content") or ""
    except (ValueError, KeyError, IndexError, TypeError) as e:
        raise LLMError(f"unexpected reply from {url}: {resp.text[:300]}") from e
    # Speed and length, from llama-server's "timings" (or the OpenAI "usage").
    timings, usage = data.get("timings") or {}, data.get("usage") or {}
    stats = {"tokens": timings.get("predicted_n") or usage.get("completion_tokens"),
             "tok_s": timings.get("predicted_per_second"),
             "prompt_tokens": timings.get("prompt_n") or usage.get("prompt_tokens")}
    # A model that "thinks" first spends most of its time there: flag it, and
    # keep only the answer.
    thinking = bool(message.get("reasoning_content")) or "<think>" in text
    if "<think>" in text:
        text = re.sub(r"<think>.*?(</think>|$)", "", text, flags=re.DOTALL).strip()
    stats["thinking"] = thinking
    _call_stats.last = stats
    return text


def _jevtypesafe(model: str, system: str, user: str, timeout: float, max_tokens: int, temperature: float) -> str:
    """jevtypesafeai.com. Configure in .env:

    YS_JEV_API_BASE  base URL of its API
    YS_JEV_API_KEY   API key
    YS_JEV_API_STYLE "openai" (OpenAI-compatible /chat/completions, default)
                     or "json" (POST {"system","prompt","model"} -> {"text"|"output"|"response"})
    """
    api_base = os.getenv("YS_JEV_API_BASE", "")
    api_key = os.getenv("YS_JEV_API_KEY", "")
    style = jev_style()
    if style == "decide":
        raise LLMError("jevtypesafe answers typed decisions, not free text; it can drive the live "
                       "performer (default engine) but not this agent")
    if not api_base:
        raise LLMError("jevtypesafe provider selected but YS_JEV_API_BASE is not set in .env")
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


# Whether the local server accepted a JSON schema (llama.cpp does; some
# OpenAI-compatible servers don't). Set to False after the first rejection.
_local_schema_ok = True


def complete(model_id: str, system: str, user: str, timeout: float | None = None,
             max_tokens: int = 1200, temperature: float = 0.8, schema: dict | None = None) -> str:
    """Return the model's text reply, or raise LLMError (including on timeout).

    schema: a JSON schema the reply must follow. Local servers enforce it
    while decoding (llama.cpp turns it into a grammar), so the reply is always
    valid JSON with only the allowed names; other providers ignore it."""
    provider, model = resolve(model_id)
    timeout = timeout or (config.LOCAL_LLM_TIMEOUT_SECONDS if provider == "local" else config.LLM_TIMEOUT_SECONDS)

    def call() -> str:
        global _local_schema_ok
        if provider == "claude":
            return _claude(model, system, user, timeout, max_tokens)
        if provider == "local":
            # Qwen-style chat templates otherwise "think" for thousands of tokens.
            extra = {"chat_template_kwargs": {"enable_thinking": False}}
            if schema is not None and _local_schema_ok:
                try:
                    return _openai_compatible(
                        model, config.LOCAL_LLM_API_BASE, "", system, user, timeout, max_tokens, temperature,
                        {**extra, "response_format": {"type": "json_schema",
                                                      "json_schema": {"name": "reply", "schema": schema}}})
                except LLMError as e:
                    text = str(e).lower()
                    if "http 400" not in text or "context" in text:
                        raise   # e.g. the prompt doesn't fit: not a schema problem
                    _local_schema_ok = False  # the server can't do schemas; ask without
            return _openai_compatible(model, config.LOCAL_LLM_API_BASE, "", system, user, timeout, max_tokens,
                                      temperature, extra)
        if provider == "jevtypesafe":
            return _jevtypesafe(model, system, user, timeout, max_tokens, temperature)
        import litellm

        response = litellm.completion(
            model=model, max_tokens=max_tokens, temperature=temperature, timeout=timeout,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        )
        return response.choices[0].message.content or ""

    def call_with_stats():
        _call_stats.last = None
        text = call()
        return text, getattr(_call_stats, "last", None)

    future = _executor.submit(call_with_stats)
    try:
        text, stats = future.result(timeout=timeout + 5)
        _call_stats.last = stats
        return text
    except concurrent.futures.TimeoutError as e:
        raise LLMError(f"{provider} call timed out after {timeout:.0f}s") from e
    except LLMError:
        raise
    except (KeyboardInterrupt, SystemExit):
        raise
    except BaseException as e:  # network, auth, bad model id, even native-extension panics
        raise LLMError(f"{provider} call failed: {type(e).__name__}: {e}") from e


# Speed and length of the last reply, per thread (set by _openai_compatible,
# read by converse for the log line).
import threading as _threading  # noqa: E402

_call_stats = _threading.local()

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
             show_full: bool = False, max_tokens: int = 1200, temperature: float = 0.8,
             timeout: float | None = None, schema: dict | None = None) -> str:
    """complete() plus a visible record of the exchange: what the agent asked
    which model, what came back and how long it took (terminal), and the full
    prompt and reply (runtime/llm_calls.jsonl). Raises LLMError like complete()."""
    import time

    from . import runtime

    provider, model = resolve(model_id)
    who = f"{provider}/{model}"
    log(f"💬 {agent} → {who}: {asks or 'request'} ({len(user):,} chars)")
    runtime.ui_thinking(agent, model)
    if show_full:
        log(f"   ┌─ prompt to {who}\n" + "\n".join(f"   │ {line}" for line in user.splitlines()) + "\n   └─")
    started = time.monotonic()
    record = {"agent": agent, "model": who, "asks": asks, "system": system, "prompt": user}
    try:
        reply = complete(model_id, system, user, timeout=timeout, max_tokens=max_tokens,
                         temperature=temperature, schema=schema)
    except LLMError as e:
        seconds = time.monotonic() - started
        log(f"💬 {agent} ← {who} ({seconds:.1f}s): ✗ {e}")
        runtime.append_llm_call({**record, "seconds": round(seconds, 2), "error": str(e)})
        runtime.ui_thinking(agent, None)
        raise
    seconds = time.monotonic() - started
    runtime.ui_thinking(agent, None)
    stats = getattr(_call_stats, "last", None) or {}
    speed = ""
    if stats.get("tokens"):
        speed = f", {stats['tokens']} tokens" + (f" at {stats['tok_s']:.0f} tok/s" if stats.get("tok_s") else "")
    log(f"💬 {agent} ← {who} ({seconds:.1f}s{speed}): {_one_line(reply)}")
    if stats.get("thinking"):
        log(f"⚠️  {who} is thinking before it answers (slow, and wasted here): turn it off in its chat "
            "template, or run llama-server with --reasoning-budget 0")
    if show_full:
        log("   ┌─ raw reply\n" + "\n".join(f"   │ {line}" for line in reply.strip().splitlines()) + "\n   └─")
    runtime.append_llm_call({**record, "seconds": round(seconds, 2), "reply": reply})
    return reply


# ---------------------------------------------------------------------------
# jevtypesafe decision API: typed questions about a situation, not free text
# ---------------------------------------------------------------------------

JEV_DECIDE_URL = "https://jevtypesafeai.com/api/v1/decide"


def jev_style() -> str:
    """"decide" (the decision API, default), or "openai"/"json" for a text endpoint."""
    return os.getenv("YS_JEV_API_STYLE", "decide").strip().lower() or "decide"


def decide(agent: str, state: str, questions: dict, *, log=print, asks: str = "", show_full: bool = False,
           timeout: float | None = None) -> dict:
    """Ask jevtypesafe's decision API typed questions about `state`.

    questions: {"name": {"type": "choice"|"score"|"noul", "instructions": ..., "criteria": ...}}
    Returns the "answers" object. Logs the exchange like converse() and
    records it in runtime/llm_calls.jsonl. Raises LLMError on any failure."""
    import time

    import requests

    from . import runtime

    url = os.getenv("YS_JEV_API_BASE", "").strip() or JEV_DECIDE_URL
    key = (os.getenv("YS_JEV_API_KEY") or os.getenv("JEV_API_KEY") or "").strip()
    timeout = timeout or config.LLM_TIMEOUT_SECONDS
    who = "jevtypesafe/decide"
    log(f"💬 {agent} → {who}: {asks or 'decision'} ({len(state):,} chars)")
    runtime.ui_thinking(agent, "jevtypesafe")
    if show_full:
        log("   ┌─ state\n" + "\n".join(f"   │ {line}" for line in state.splitlines())
            + "\n   ├─ questions\n   │ " + json.dumps(questions) + "\n   └─")
    record = {"agent": agent, "model": who, "asks": asks, "prompt": state, "questions": questions}
    started = time.monotonic()
    try:
        if not key:
            raise LLMError("no jevtypesafe API key: set YS_JEV_API_KEY=... in .env")
        try:
            resp = requests.post(url, headers={"Authorization": f"Bearer {key}"},
                                 json={"state": state, "questions": questions}, timeout=timeout)
        except requests.Timeout as e:
            raise LLMError(f"jevtypesafe call timed out after {timeout:.0f}s") from e
        except requests.RequestException as e:
            raise LLMError(f"cannot reach {url}: {e}") from e
        if resp.status_code >= 400:
            raise LLMError(f"jevtypesafe HTTP {resp.status_code}: {resp.text[:300]}")
        try:
            answers = resp.json()["answers"]
            if not isinstance(answers, dict):
                raise TypeError("answers is not an object")
        except (ValueError, KeyError, TypeError) as e:
            raise LLMError(f"unexpected jevtypesafe reply: {resp.text[:300]}") from e
    except LLMError as e:
        seconds = time.monotonic() - started
        log(f"💬 {agent} ← {who} ({seconds:.1f}s): ✗ {e}")
        runtime.append_llm_call({**record, "seconds": round(seconds, 2), "error": str(e)})
        runtime.ui_thinking(agent, None)
        raise
    seconds = time.monotonic() - started
    runtime.ui_thinking(agent, None)
    log(f"💬 {agent} ← {who} ({seconds:.1f}s): {_one_line(json.dumps(answers))}")
    runtime.append_llm_call({**record, "seconds": round(seconds, 2), "reply": resp.text})
    return answers
