"""
Thin LLM client with hard timeouts and JSON extraction.

Model ids follow LiteLLM conventions ("gpt-5-mini", "anthropic/claude-...",
"ollama/...") plus "local/<name>", which routes to the local llama.cpp server.
"""

from __future__ import annotations

import concurrent.futures
import json
import re

import config

_executor = concurrent.futures.ThreadPoolExecutor(max_workers=2, thread_name_prefix="llm")


class LLMError(RuntimeError):
    pass


def _completion_kwargs(model_id: str) -> dict:
    if model_id.startswith("local/"):
        return {
            "model": "openai/" + model_id.split("/", 1)[1],
            "api_base": config.LOCAL_LLM_API_BASE,
            "api_key": "sk-no-key-required",
            # Qwen-style chat templates otherwise "think" for thousands of tokens.
            "extra_body": {"chat_template_kwargs": {"enable_thinking": False}},
        }
    return {"model": model_id}


def complete(model_id: str, system: str, user: str, timeout: float | None = None,
             max_tokens: int = 1200, temperature: float = 0.8) -> str:
    """Return the model's text reply, or raise LLMError (including on timeout)."""
    timeout = timeout or config.LLM_TIMEOUT_SECONDS

    def call() -> str:
        import litellm

        response = litellm.completion(
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            max_tokens=max_tokens,
            temperature=temperature,
            timeout=timeout,
            **_completion_kwargs(model_id),
        )
        return response.choices[0].message.content or ""

    future = _executor.submit(call)
    try:
        return future.result(timeout=timeout + 5)
    except concurrent.futures.TimeoutError as e:
        raise LLMError(f"LLM call timed out after {timeout:.0f}s") from e
    except Exception as e:  # network, auth, bad model id...
        raise LLMError(f"LLM call failed: {e}") from e


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
