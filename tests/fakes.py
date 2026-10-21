"""Test doubles."""

from __future__ import annotations

import hashlib


class FakeEmbedder:
    """Deterministic 4-dim vectors; records calls; can be told to fail."""

    def __init__(self, dim: int = 4) -> None:
        self.dim = dim
        self.calls = 0
        self.texts: list[str] = []
        self.fail_with: Exception | None = None

    def _vec(self, text: str) -> list[float]:
        digest = hashlib.sha256(text.encode()).digest()
        return [b / 255 for b in digest[: self.dim]]

    def embed_documents(self, texts, on_batch=None):
        if self.fail_with:
            raise self.fail_with
        self.calls += 1
        self.texts.extend(texts)
        if on_batch:
            on_batch(len(texts), len(texts))
        return [self._vec(t) for t in texts]

    def embed_query(self, text):
        return self._vec(text)

    def close(self):
        pass


class KeywordEmbedder(FakeEmbedder):
    """Vectors by vocabulary presence, so similarity follows shared words."""

    VOCAB = ("audit", "pool", "checkout", "secret", "parking", "pets")

    def __init__(self, dim: int = 4) -> None:
        super().__init__(dim)

    def _vec(self, text: str) -> list[float]:
        text = text.lower()
        raw = [1.0 if w in text else 0.0 for w in self.VOCAB]
        vec = [raw[i] + raw[i + self.dim] if i + self.dim < len(raw) else raw[i] for i in range(self.dim)]
        vec = [v + 0.01 for v in vec]  # never all-zero
        norm = sum(v * v for v in vec) ** 0.5
        return [v / norm for v in vec]


class FakeReranker:
    """Scores documents by how often `prefer` appears."""

    def __init__(self, prefer: str) -> None:
        self.prefer = prefer.lower()

    def scores(self, query, documents):
        return [d.lower().count(self.prefer) for d in documents]
