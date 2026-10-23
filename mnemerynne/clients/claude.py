"""Claude (Anthropic's Messages API) as Erynne's agent model, with the person's own key.

Speaks the agent's interface (LLM.chat_tools: OpenAI-style messages and
tools in, a ToolTurn out) and translates to and from the Messages API. Each
reply's content blocks, thinking included, are kept by tool-call id and sent
back unchanged on the next step, as the API requires. The agent never edits
this turn's history for Claude (its context budget is far above a turn), so
those blocks stay valid.
"""

from __future__ import annotations

import json
from typing import Any

import anthropic

from mnemerynne.clients.llm import ToolCall, ToolTurn

EFFORT = "medium"           # chat: Claude decides how hard to think, within this
MAX_TOKENS = 16000          # per step, non-streaming
FALLBACK_BETA = "server-side-fallback-2026-07-01"
# A declined request is retried on another model by the API itself; Haiku has no fallback.
FALLBACK_MODELS = {"claude-opus-5-5", "claude-sonnet-5-5"}
REFUSED_TEXT = "Sorry, Claude declined to answer that. You can ask without Claude instead."


class ClaudeKeyError(ValueError):
    """The key can't be used; the message is safe to show to its owner."""


def to_tools(specs: list[dict]) -> list[dict]:
    """OpenAI-style function specs as Messages API tools."""
    return [{"name": s["function"]["name"], "description": s["function"].get("description") or "",
             "input_schema": s["function"].get("parameters") or {"type": "object", "properties": {}}}
            for s in specs]


def _text_block(text: str) -> dict:
    return {"type": "text", "text": text}


def to_messages(messages: list[dict], raw: dict[str, tuple[list, list]]) -> tuple[str, list[dict], list[dict]]:
    """(system, messages, tools the history was made with) from the agent's
    OpenAI-style conversation. Tool results become tool_result blocks in one
    user message; an assistant turn with tool calls is sent back as Claude
    wrote it (raw, by its first call's id)."""
    system: list[str] = []
    out: list[dict] = []
    history_tools: list[dict] = []

    def add_user(blocks: list[dict]) -> None:
        if out and out[-1]["role"] == "user":
            out[-1]["content"].extend(blocks)
        else:
            out.append({"role": "user", "content": blocks})

    for m in messages:
        role, content = m["role"], m.get("content") or ""
        if role == "system":
            system.append(content)
        elif role == "user":
            add_user([_text_block(content)])
        elif role == "tool":
            add_user([{"type": "tool_result", "tool_use_id": m["tool_call_id"], "content": content}])
        elif m.get("tool_calls"):
            first = m["tool_calls"][0]["id"]
            if first in raw:
                blocks, history_tools = raw[first]
            else:
                blocks = ([_text_block(content)] if content else []) + [
                    {"type": "tool_use", "id": c["id"], "name": c["function"]["name"],
                     "input": json.loads(c["function"]["arguments"] or "{}")} for c in m["tool_calls"]]
            out.append({"role": "assistant", "content": blocks})
        elif content:
            out.append({"role": "assistant", "content": content})
    return "\n\n".join(system), out, history_tools


class ClaudeLLM:
    """One person's Claude, for one question (made per run, closed after)."""

    def __init__(self, api_key: str, model: str, *, timeout: float = 300.0, client: Any = None) -> None:
        self.model = model
        self._client = client or anthropic.AsyncAnthropic(api_key=api_key, timeout=timeout, max_retries=2)
        self._raw: dict[str, tuple[list, list]] = {}   # first tool-call id -> (content blocks, tools sent)

    async def chat_tools(self, messages: list[dict], tools: list[dict], *, temperature: float = 0.2,
                         max_tokens: int = 1024, thinking: bool = True) -> ToolTurn:
        """One step. temperature, max_tokens and thinking are the local model's
        knobs: Claude takes none of them (adaptive thinking, EFFORT, MAX_TOKENS)."""
        system, convo, history_tools = to_messages(messages, self._raw)
        offered = to_tools(tools)
        kwargs: dict[str, Any] = {"model": self.model, "max_tokens": MAX_TOKENS, "system": system,
                                  "messages": convo, "output_config": {"effort": EFFORT},
                                  # The turn's steps share a growing prefix: cache it.
                                  "cache_control": {"type": "ephemeral"}}
        # Once tools were used, the API needs them defined; "none" when the agent offers none (out of steps).
        sent = offered or history_tools
        if sent:
            kwargs["tools"] = sent
            kwargs["tool_choice"] = {"type": "auto" if offered else "none"}
        if self.model in FALLBACK_MODELS:
            kwargs["betas"] = [FALLBACK_BETA]
            kwargs["fallbacks"] = "default"
        response = await self._client.beta.messages.create(**kwargs)
        if response.stop_reason == "refusal":
            return ToolTurn(REFUSED_TEXT, [], "refusal")
        text = "".join(b.text for b in response.content if b.type == "text")
        calls = [ToolCall(b.id, b.name, dict(b.input or {})) for b in response.content if b.type == "tool_use"]
        if calls:
            self._raw[calls[0].id] = (list(response.content), sent)
        return ToolTurn(text.strip(), calls, "length" if response.stop_reason == "max_tokens" else
                        response.stop_reason)

    async def reachable(self) -> bool:
        return True   # no free health check; a failed first step falls back to the normal flow

    async def aclose(self) -> None:
        await self._client.close()


async def check_key(api_key: str, model: str) -> None:
    """Ask Anthropic whether this key may use this model (no tokens spent)."""
    client = anthropic.AsyncAnthropic(api_key=api_key, timeout=20.0, max_retries=1)
    try:
        await client.models.retrieve(model)
    except anthropic.AuthenticationError:
        raise ClaudeKeyError("Anthropic didn't accept that key.") from None
    except (anthropic.PermissionDeniedError, anthropic.NotFoundError):
        raise ClaudeKeyError("That key can't use this model; pick another or check the key's workspace.") from None
    except anthropic.APIConnectionError:
        raise ClaudeKeyError("Couldn't reach Anthropic to check the key; try again in a moment.") from None
    except anthropic.APIStatusError as exc:
        raise ClaudeKeyError(f"Anthropic answered HTTP {exc.status_code} when checking the key.") from None
    finally:
        await client.close()
