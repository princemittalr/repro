"""Thin wrapper around the OpenAI SDK, pointed at Nebius Token Factory.

Token Factory exposes an OpenAI-compatible API, so the official `openai`
client works unchanged once `base_url` and `api_key` are swapped. Every call
goes through `chat()` so tokens and latency are recorded for the cost panel.
"""

from __future__ import annotations

import json
import re
import time
from functools import lru_cache

from openai import BadRequestError, OpenAI

from app.config import settings
from app.usage import UsageTracker


@lru_cache(maxsize=1)
def get_client() -> OpenAI:
    """Return an OpenAI client that talks to Nebius Token Factory."""
    if not settings.nebius_api_key or settings.nebius_api_key.startswith("your-"):
        raise RuntimeError(
            "NEBIUS_API_KEY is missing. Copy .env.example to .env and paste your Token Factory key."
        )
    return OpenAI(api_key=settings.nebius_api_key, base_url=settings.nebius_base_url, timeout=300, max_retries=2)


class TruncatedReply(ValueError):
    """The model hit max_tokens before writing any answer."""


def friendly_error(exc: Exception) -> str:
    """A one-line explanation of a failure, for the UI and the CLI."""
    status = getattr(exc, "status_code", None)
    if status == 402:
        return "Nebius Token Factory credits are exhausted (HTTP 402). Add credit to the account to run live."
    if status == 401:
        return "Nebius Token Factory rejected the API key (HTTP 401). Check NEBIUS_API_KEY in .env."
    if status == 429:
        return "Nebius Token Factory rate limit reached (HTTP 429). Wait a minute and try again."
    return f"{type(exc).__name__}: {exc}"[:400]


def chat(
    *,
    stage: str,
    model: str,
    messages: list[dict],
    tracker: UsageTracker | None = None,
    max_tokens: int = 4096,
    temperature: float = 0.2,
    extra_body: dict | None = None,
    response_format: dict | None = None,
    thinking: bool | None = None,
) -> str:
    """Send one chat completion and return the assistant text.

    `thinking=False` switches off the reasoning phase of Nemotron reasoning
    models (faster and cheaper for plain transcription); None keeps the
    model's default.
    """
    start = time.perf_counter()
    kwargs = {"response_format": response_format} if response_format else {}
    body = dict(extra_body or {})
    if thinking is not None:
        body["chat_template_kwargs"] = {**body.get("chat_template_kwargs", {}), "enable_thinking": thinking}
    resp = get_client().chat.completions.create(
        model=model,
        messages=messages,
        max_tokens=max_tokens,
        temperature=temperature,
        extra_body=body,
        **kwargs,
    )
    if tracker is not None:
        tracker.record(stage, model, resp.usage, time.perf_counter() - start)
    choice = resp.choices[0]
    if not choice.message.content and choice.finish_reason == "length":
        # Reasoning models can spend the whole budget thinking and never answer.
        raise TruncatedReply(f"{model} used all {max_tokens} tokens before answering")
    return choice.message.content or ""


def chat_tools(
    *,
    stage: str,
    model: str,
    messages: list[dict],
    tools: list[dict],
    tracker: UsageTracker | None = None,
    max_tokens: int = 8192,
    temperature: float = 0.2,
    thinking: bool | None = None,
):
    """One tool-calling turn: returns the assistant message (text and/or tool calls).

    Used by agents that loop: call, execute the requested tools, append the
    results as `tool` messages, call again.
    """
    start = time.perf_counter()
    body = {"chat_template_kwargs": {"enable_thinking": thinking}} if thinking is not None else {}
    resp = get_client().chat.completions.create(
        model=model, messages=messages, tools=tools, tool_choice="auto",
        max_tokens=max_tokens, temperature=temperature, extra_body=body,
    )
    if tracker is not None:
        tracker.record(stage, model, resp.usage, time.perf_counter() - start)
    choice = resp.choices[0]
    if not choice.message.content and not choice.message.tool_calls and choice.finish_reason == "length":
        raise TruncatedReply(f"{model} used all {max_tokens} tokens before answering")
    return choice.message


# Models that rejected structured output once; we stop asking them for it.
_NO_STRUCTURED_OUTPUT: set[str] = set()


def chat_json(*, schema: dict, schema_name: str, **kwargs) -> dict:
    """Like `chat`, but asks the server to constrain output to `schema`.

    Token Factory serves models with guided decoding, which guarantees
    syntactically valid JSON. If a model rejects `response_format`, we fall
    back to plain prompting plus `extract_json`.
    """
    model = kwargs["model"]
    if model not in _NO_STRUCTURED_OUTPUT:
        try:
            text = chat(
                response_format={"type": "json_schema", "json_schema": {"name": schema_name, "schema": schema}},
                **kwargs,
            )
            return extract_json(text)
        except BadRequestError:
            _NO_STRUCTURED_OUTPUT.add(model)
    return extract_json(chat(**kwargs))


_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)


def extract_json(text: str) -> dict:
    """Pull the JSON object out of a model reply.

    Handles reasoning blocks (<think>...</think>), ```json fences and any
    chatter before or after the object.
    """
    cleaned = _THINK_RE.sub("", text).strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", cleaned, re.DOTALL)
    if fenced:
        cleaned = fenced.group(1)
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("No JSON object found in model reply")
    return json.loads(cleaned[start : end + 1])
