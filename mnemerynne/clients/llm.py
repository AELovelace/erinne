"""Chat client for llama-server's OpenAI-compatible /v1/chat/completions.

Used by evaluation and data generation directly; the LangGraph answer flow
talks to the same endpoint through langchain-openai.
"""

from __future__ import annotations

import ast
import asyncio
import contextvars
import json
import re
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

import httpx

from mnemerynne.config import Settings

# Tokens one question used, for chat's token counter: graph.run_turn sets a fresh dict, and every
# model call made while answering adds to it (Claude's under "external" too).
USAGE: contextvars.ContextVar[dict | None] = contextvars.ContextVar("mnemerynne_usage", default=None)


def new_usage() -> dict:
    return {"in": 0, "out": 0, "external": 0, "estimated": False}


def count_usage(tokens_in: int, tokens_out: int, *, external: bool = False, estimated: bool = False) -> None:
    usage = USAGE.get()
    if usage is None:
        return
    usage["in"] += int(tokens_in)
    usage["out"] += int(tokens_out)
    if external:
        usage["external"] += int(tokens_in) + int(tokens_out)
    if estimated:
        usage["estimated"] = True


_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_OPEN_THINK_RE = re.compile(r"<think>.*\Z", re.DOTALL | re.IGNORECASE)


def strip_reasoning(text: str) -> str:
    """Remove <think>…</think> blocks some models emit inline, and a final
    one the model never closed (it ran out of tokens while thinking)."""
    return _OPEN_THINK_RE.sub("", _THINK_RE.sub("", text or "")).strip()


class ThinkFilter:
    """Drops <think>…</think> from streamed text, even when a tag is split
    across chunks. feed() returns what can be shown so far; close() the rest
    (nothing, if the model stopped while still thinking)."""

    OPEN, CLOSE = "<think>", "</think>"

    def __init__(self) -> None:
        self._buf = ""
        self._thinking = False

    def feed(self, text: str) -> str:
        self._buf += text
        out = []
        while True:
            tag = self.CLOSE if self._thinking else self.OPEN
            at = self._buf.lower().find(tag)
            if at >= 0:
                if not self._thinking:
                    out.append(self._buf[:at])
                self._buf = self._buf[at + len(tag):]
                self._thinking = not self._thinking
                continue
            # Keep back anything that could be the start of a tag.
            keep = next((n for n in range(min(len(tag) - 1, len(self._buf)), 0, -1)
                         if tag.startswith(self._buf[-n:].lower())), 0)
            if not self._thinking:
                out.append(self._buf[:len(self._buf) - keep])
            self._buf = self._buf[len(self._buf) - keep:]
            return "".join(out)

    def close(self) -> str:
        rest, self._buf = ("" if self._thinking else self._buf), ""
        return rest


class BadModelOutput(ValueError):
    """A schema-constrained call didn't return a JSON object; the message says why."""


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict
    error: str | None = None   # the arguments weren't a JSON object: tell the model, don't run it


@dataclass
class ToolTurn:
    """One model turn with tools offered: text, or tool calls to run (or both)."""
    content: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    finish_reason: str | None = None


# Tool calls the server left in the text instead of parsing them: LFM2's
# <|tool_call_start|>[name(arg=1)]<|tool_call_end|> and Qwen/Hermes's
# <tool_call>{"name": ..., "arguments": {...}}</tool_call>.
_LFM_CALLS = re.compile(r"<\|tool_call_start\|>(.*?)<\|tool_call_end\|>", re.DOTALL)
_HERMES_CALLS = re.compile(r"<tool_call>(.*?)</tool_call>", re.DOTALL)


def _call_from(raw: dict, n: int) -> ToolCall:
    fn = raw.get("function") or {}
    args = fn.get("arguments")
    error = None
    if isinstance(args, str):
        try:
            args = json.loads(args) if args.strip() else {}
        except json.JSONDecodeError:
            args, error = {}, "The arguments weren't valid JSON."
    if not isinstance(args, dict):
        args, error = {}, error or "The arguments must be a JSON object."
    return ToolCall(raw.get("id") or f"call_{n}", str(fn.get("name") or ""), args, error)


def parse_inline_calls(text: str) -> tuple[str, list[ToolCall]]:
    """Tool calls written into the reply text; returns (the text without them, calls)."""
    calls: list[ToolCall] = []
    for match in _LFM_CALLS.finditer(text):
        try:
            tree = ast.parse(match.group(1).strip(), mode="eval").body
            items = tree.elts if isinstance(tree, ast.List) else [tree]
            for item in items:
                if not isinstance(item, ast.Call) or not isinstance(item.func, ast.Name):
                    raise ValueError
                calls.append(ToolCall(f"call_{len(calls)}", item.func.id,
                                      {k.arg: ast.literal_eval(k.value) for k in item.keywords if k.arg}))
        except (SyntaxError, ValueError):
            calls.append(ToolCall(f"call_{len(calls)}", "", {}, "Couldn't read that tool call."))
    for match in _HERMES_CALLS.finditer(text):
        try:
            raw = json.loads(match.group(1))
            calls.append(_call_from({"function": {"name": raw.get("name"), "arguments": raw.get("arguments") or {}}},
                                    len(calls)))
        except (json.JSONDecodeError, AttributeError):
            calls.append(ToolCall(f"call_{len(calls)}", "", {}, "Couldn't read that tool call."))
    if calls:
        text = _HERMES_CALLS.sub("", _LFM_CALLS.sub("", text))
    return text, calls


def strict_schema(schema: Any) -> Any:
    """OpenAI strict structured outputs: every object closed and fully required,
    and no keywords it rejects (e.g. maxItems)."""
    if isinstance(schema, dict):
        out = {k: strict_schema(v) for k, v in schema.items() if k not in ("maxItems", "minItems")}
        if out.get("type") == "object":
            out["additionalProperties"] = False
            out["required"] = list(out.get("properties", {}))
        return out
    if isinstance(schema, list):
        return [strict_schema(s) for s in schema]
    return schema


class BudgetExceeded(RuntimeError):
    """The remote teacher's spending limit was reached; stop generating."""


class LLM:
    """flavor "llamacpp" (default, local) or "openai" (remote API, needs api_key).

    Remote calls track spend from the API's reported usage (hidden reasoning
    tokens included, as completion tokens). With budget_usd set, a call made
    after the budget is spent raises BudgetExceeded instead of being sent.
    """

    price_in_per_m: float = 0.0
    price_out_per_m: float = 0.0
    budget_usd: float | None = None
    spent_usd: float = 0.0
    tokens_in: int = 0
    tokens_out: int = 0

    def _check_budget(self) -> None:
        if self.budget_usd is not None and self.spent_usd >= self.budget_usd:
            raise BudgetExceeded(f"Teacher budget of ${self.budget_usd:.2f} reached (spent ${self.spent_usd:.2f}).")

    def _record_usage(self, usage: dict | None) -> None:
        if not usage:
            return
        tin, tout = int(usage.get("prompt_tokens", 0)), int(usage.get("completion_tokens", 0))
        count_usage(tin, tout)
        self.tokens_in += tin
        self.tokens_out += tout
        self.spent_usd += tin / 1e6 * self.price_in_per_m + tout / 1e6 * self.price_out_per_m

    def __init__(self, base_url: str, *, model: str = "local", timeout: float = 120.0,
                 client: httpx.AsyncClient | None = None, flavor: str = "llamacpp",
                 api_key: str = "", max_retries: int = 4, reasoning_tokens: int = 0) -> None:
        self.url = base_url.rstrip("/") + "/v1/chat/completions"
        self.model = model
        self.flavor = flavor
        self.max_retries = max_retries
        # Added to every local call's max_tokens: room to think before the answer.
        self.reasoning_tokens = reasoning_tokens
        self._unsupported: set[str] = set()  # parameters this model rejected once; never sent again
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self._client = client or httpx.AsyncClient(timeout=timeout, headers=headers)

    @classmethod
    def from_settings(cls, settings: Settings, base_url: str | None = None,
                      transport: httpx.AsyncBaseTransport | None = None) -> "LLM":
        """transport: e.g. balancer.BalancedTransport, which sends requests to balancer nodes."""
        client = httpx.AsyncClient(timeout=settings.llm_timeout, transport=transport) if transport else None
        return cls(base_url or settings.llm_url, timeout=settings.llm_timeout, client=client,
                   reasoning_tokens=settings.llm_reasoning_tokens)

    @classmethod
    def teacher(cls, settings: Settings, base_url: str | None = None) -> "LLM":
        """The model that writes training/evaluation data: local by default,
        or a remote OpenAI-compatible API when MNEMERYNNE_TEACHER_FLAVOR=openai."""
        timeout = max(settings.llm_timeout, 300)
        if settings.teacher_flavor == "openai":
            if not settings.teacher_api_key:
                raise ValueError("MNEMERYNNE_TEACHER_API_KEY is required for an OpenAI teacher.")
            llm = cls(base_url or settings.teacher_url, model=settings.teacher_model, timeout=timeout,
                      flavor="openai", api_key=settings.teacher_api_key)
            llm.price_in_per_m, llm.price_out_per_m = settings.teacher_price_in, settings.teacher_price_out
            return llm
        return cls(base_url or settings.llm_url, timeout=timeout)

    @property
    def is_remote(self) -> bool:
        return self.flavor == "openai"

    def _payload(self, messages, temperature, max_tokens, json_schema, thinking, stream) -> dict[str, Any]:
        payload: dict[str, Any] = {"model": self.model, "messages": messages, "stream": stream}
        if stream:
            payload["stream_options"] = {"include_usage": True}   # the last chunk says what it used
        if self.flavor == "openai":
            # GPT-5-family reasoning models: hidden reasoning counts toward this
            # budget, temperature is fixed, effort is set instead.
            payload["max_completion_tokens"] = max_tokens + 2000
            # Ask for little hidden reasoning (cheaper); both parameters are sent
            # and _post drops whichever this model rejects.
            payload["reasoning_effort"] = "low"
            payload["temperature"] = temperature
            for p in self._unsupported:
                payload.pop(p, None)
            if "max_completion_tokens" in self._unsupported:
                payload["max_tokens"] = max_tokens + 2000
            schema = strict_schema(json_schema) if json_schema is not None else None
            strict = True
        else:
            payload["temperature"] = temperature
            payload["max_tokens"] = max_tokens + self.reasoning_tokens
            # Read by chat templates that support it (Qwen3); ignored by others.
            payload["chat_template_kwargs"] = {"enable_thinking": bool(thinking) and json_schema is None}
            schema, strict = json_schema, None
        if schema is not None:
            fmt: dict[str, Any] = {"name": "response", "schema": schema}
            if strict:
                fmt["strict"] = True
            payload["response_format"] = {"type": "json_schema", "json_schema": fmt}
        return payload

    async def _post(self, payload: dict) -> httpx.Response:
        """POST with retries on rate limits and transient server errors (remote APIs).

        Models differ in which sampling parameters they accept (reasoning models
        reject temperature, older ones reject reasoning_effort): a 400 naming one
        of those drops it and retries.
        """
        delay = 2.0
        for attempt in range(self.max_retries + 1):
            response = await self._client.post(self.url, json=payload)
            if response.status_code == 400 and self.flavor == "openai":
                text = response.text.lower()
                dropped = [p for p in ("temperature", "reasoning_effort", "max_completion_tokens")
                           if p in payload and p in text]
                if dropped and attempt < self.max_retries:
                    for p in dropped:
                        payload.pop(p)
                        self._unsupported.add(p)
                    if "max_completion_tokens" in dropped:
                        payload["max_tokens"] = 2500
                    continue
            if response.status_code in (429, 500, 502, 503, 504) and attempt < self.max_retries:
                retry_after = response.headers.get("retry-after")
                await asyncio.sleep(float(retry_after) if retry_after and retry_after.isdigit() else delay)
                delay *= 2
                continue
            response.raise_for_status()
            return response
        response.raise_for_status()
        return response

    async def _complete(self, messages, temperature, max_tokens, json_schema, thinking) -> tuple[str, dict]:
        """(content without reasoning, the raw choice)."""
        self._check_budget()
        response = await self._post(self._payload(messages, temperature, max_tokens, json_schema, thinking, False))
        body = response.json()
        self._record_usage(body.get("usage"))
        choice = body["choices"][0]
        return strip_reasoning(choice["message"].get("content") or ""), choice

    async def chat(self, messages: list[dict[str, str]], *, temperature: float = 0.2, max_tokens: int = 512,
                   json_schema: dict | None = None, thinking: bool = False) -> str:
        return (await self._complete(messages, temperature, max_tokens, json_schema, thinking))[0]

    async def chat_json(self, messages: list[dict[str, str]], schema: dict, *, temperature: float = 0.2,
                        max_tokens: int = 512, thinking: bool = False) -> dict:
        """Schema-constrained call; returns the parsed object, or raises
        BadModelOutput saying what came back instead."""
        content, choice = await self._complete(messages, temperature, max_tokens, schema, thinking)
        try:
            out = json.loads(content)
        except json.JSONDecodeError:
            out = None
        if isinstance(out, dict):
            return out
        message = choice.get("message") or {}
        reasoning = message.get("reasoning_content") or ""
        raw = message.get("content") or ""
        why = []
        if choice.get("finish_reason") == "length":
            why.append(f"it hit the {max_tokens + self.reasoning_tokens}-token limit")
        if reasoning or "<think>" in raw.lower():
            why.append(f"it spent {len(reasoning) or len(raw)} characters thinking (a reasoning model, or "
                       f"a chat template that ignores enable_thinking=false)")
        raise BadModelOutput("The model didn't return JSON" + (": " + " and ".join(why) if why else "")
                             + f". Reply began: {content[:120]!r}")

    async def chat_tools(self, messages: list[dict], tools: list[dict], *, temperature: float = 0.2,
                         max_tokens: int = 1024, thinking: bool = True) -> ToolTurn:
        """One turn with OpenAI-style `tools` offered (llama-server needs --jinja).
        Calls the server parsed come back in tool_calls; ones left in the text
        (some chat templates) are parsed here."""
        self._check_budget()
        payload = self._payload(messages, temperature, max_tokens, None, thinking, False)
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        body = (await self._post(payload)).json()
        self._record_usage(body.get("usage"))
        choice = body["choices"][0]
        message = choice.get("message") or {}
        calls = [_call_from(c, n) for n, c in enumerate(message.get("tool_calls") or [])]
        content = message.get("content") or ""
        if not calls:
            content, calls = parse_inline_calls(content)
        return ToolTurn(strip_reasoning(content), calls, choice.get("finish_reason"))

    async def stream(self, messages: list[dict[str, str]], *, temperature: float = 0.2, max_tokens: int = 512,
                     json_schema: dict | None = None) -> AsyncIterator[str]:
        """Yield content deltas as they arrive (server-sent events).

        Remote APIs: rejected sampling parameters are dropped and rate limits
        retried before any text is yielded (same rules as `_post`).
        """
        payload = self._payload(messages, temperature, max_tokens, json_schema, False, True)
        delay = 2.0
        for attempt in range(self.max_retries + 1):
            async with self._client.stream("POST", self.url, json=payload) as response:
                if response.status_code >= 400 and self.flavor == "openai" and attempt < self.max_retries:
                    text = (await response.aread()).decode(errors="replace").lower()
                    if response.status_code == 400:
                        dropped = [p for p in ("temperature", "reasoning_effort") if p in payload and p in text]
                        if dropped:
                            for p in dropped:
                                payload.pop(p)
                                self._unsupported.add(p)
                            continue
                    if response.status_code in (429, 500, 502, 503, 504):
                        retry_after = response.headers.get("retry-after")
                        await asyncio.sleep(float(retry_after) if retry_after and retry_after.isdigit() else delay)
                        delay *= 2
                        continue
                response.raise_for_status()
                counted, written = False, 0
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    chunk = json.loads(data)
                    if chunk.get("usage"):
                        self._record_usage(chunk["usage"])
                        counted = True
                    choices = chunk.get("choices") or []
                    delta = choices[0].get("delta", {}).get("content") if choices else None
                    if delta:
                        written += len(delta)
                        yield delta
                if not counted:   # a server that doesn't report usage: about 4 characters a token
                    asked = sum(len(str(m.get("content") or "")) for m in messages)
                    count_usage(asked // 4, written // 4, estimated=True)
                return

    async def health(self) -> bool:
        try:
            r = await self._client.get(self.url.rsplit("/v1/", 1)[0] + "/health", timeout=5.0)
            return r.status_code == 200
        except httpx.HTTPError:
            return False

    async def reachable(self) -> bool:
        """The server answers and has a model loaded: llama-server's /health,
        or /v1/models on servers and proxies without it."""
        base = self.url.rsplit("/v1/", 1)[0]
        for path in ("/health", "/v1/models"):
            try:
                r = await self._client.get(base + path, timeout=5.0)
            except httpx.HTTPError:
                return False
            if r.status_code == 200:
                return True
            if r.status_code != 404:
                return False   # e.g. 503 while llama-server is loading the model
        return False

    async def aclose(self) -> None:
        await self._client.aclose()
