"""Long-term user memory: facts users state about themselves, kept across chats.

Runs after an answer is sent (never delays it). Only the user's own message is
read, never documents or answers. Extracted facts pass a hard filter that
rejects anything resembling secrets or personal data before being stored in
the LangGraph store under ("user", <id>, "memories").
"""

from __future__ import annotations

import asyncio
import logging
import re
import uuid
from datetime import datetime, timezone

from erinne.graph import memory_namespace

log = logging.getLogger("erinne.memory")

MEMORY_SCHEMA = {
    "type": "object",
    "properties": {"facts": {"type": "array", "items": {"type": "string"}, "maxItems": 3}},
    "required": ["facts"],
}

_PROMPT = """A hotel staff member wrote the message below to an assistant. Extract facts the person states
about THEMSELVES that would help answer their future questions: which property/hotel they work at,
their role or department, their shift, or how they like answers (e.g. "keep it brief").

Rules:
- Only facts the person explicitly states about themselves. Questions are not facts.
- Never include facts about guests, colleagues or other people, document content, or anything
  that looks like a password, PIN, code, card number, phone number or email address.
- Write each fact as a short third-person statement, e.g. "Works the night shift." or "Prefers short answers."
- Return an empty list if there is nothing to remember (this is the usual case).

Message: \"\"\"{message}\"\"\""""

# Hard filter applied to whatever the model returns.
_SENSITIVE = [
    re.compile(r"\b(?:\d[ -]?){13,19}\b"),                     # card-like numbers
    re.compile(r"\d{5,}"),                                       # long codes, phone numbers
    re.compile(r"\b(pass(word|code|phrase)?|pin|login|credential|ssn|social security|cvv|card)\b", re.I),
    re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+"),                      # email addresses
    re.compile(r"\b(room \d+|reservation|confirmation (number|code|#))\b", re.I),  # guest details
]
_REMEMBER = re.compile(r"\bremember\b", re.I)


def is_safe(fact: str) -> bool:
    return bool(fact.strip()) and len(fact) <= 200 and not any(p.search(fact) for p in _SENSITIVE)


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", text.lower()).strip()


async def extract(llm, message: str) -> list[str]:
    out = await llm.chat_json([{"role": "user", "content": _PROMPT.format(message=message[:2000])}],
                              MEMORY_SCHEMA, temperature=0.0, max_tokens=200)
    return [f.strip() for f in out.get("facts", []) if isinstance(f, str)]


async def remember(store, user_id: int, facts: list[str], source: str, limit: int) -> list[str]:
    """Store new, safe, non-duplicate facts; returns what was saved."""
    namespace = memory_namespace(user_id)
    existing = await store.asearch(namespace, limit=limit)
    known = [_norm(i.value["text"]) for i in existing]
    saved = []
    for fact in facts:
        if not is_safe(fact):
            log.info("Rejected a memory candidate by the safety filter.")
            continue
        n = _norm(fact)
        if not n or any(n == k or n in k or k in n for k in known):
            continue
        if len(known) >= limit:
            break
        await store.aput(namespace, uuid.uuid4().hex, {
            "text": fact, "source": source, "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds")})
        known.append(n)
        saved.append(fact)
    return saved


def make_writer(settings, llm, store, background: set):
    """The background hook called after each answer: (user_id, conversation_id, question)."""
    mode = settings.memory_mode

    async def write(user_id: int, conversation_id: str, question: str) -> list[str]:
        if mode == "off" or (mode == "explicit" and not _REMEMBER.search(question)):
            return []
        try:
            facts = await extract(llm, question)
            return await remember(store, user_id, facts, conversation_id, settings.memory_max_per_user)
        except Exception:  # never let memory break chat
            log.exception("Memory extraction failed")
            return []
        finally:
            task = asyncio.current_task()
            if task:
                background.discard(task)

    return write


async def list_memories(store, user_id: int, limit: int = 100) -> list[dict]:
    items = await store.asearch(memory_namespace(user_id), limit=limit)
    return [{"id": i.key, "text": i.value["text"], "created_at": i.value.get("created_at")} for i in items]


async def forget(store, user_id: int, key: str | None = None) -> int:
    """Delete one memory, or all of a user's memories when key is None."""
    namespace = memory_namespace(user_id)
    if key is not None:
        if await store.aget(namespace, key) is None:
            return 0
        await store.adelete(namespace, key)
        return 1
    items = await store.asearch(namespace, limit=10_000)
    for item in items:
        await store.adelete(namespace, item.key)
    return len(items)
