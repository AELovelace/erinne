"""Embedding client for llama-server's OpenAI-compatible /v1/embeddings.

Synchronous on purpose: it runs in the ingestion worker and, for queries,
inside a thread.
"""

from __future__ import annotations

import struct
from typing import Sequence

import httpx

from erinne.config import Settings


class EmbeddingError(RuntimeError):
    pass


class Embedder:
    def __init__(self, settings: Settings, client: httpx.Client | None = None) -> None:
        self.url = settings.embed_url.rstrip("/") + "/v1/embeddings"
        self.model = settings.embed_model
        self.dim = settings.embed_dim
        self.batch_size = max(1, settings.embed_batch_size)
        self.query_prefix = settings.embed_query_prefix
        self.doc_prefix = settings.embed_doc_prefix
        self._client = client or httpx.Client(timeout=settings.embed_timeout)

    def embed_documents(self, texts: Sequence[str], on_batch=None) -> list[list[float]]:
        """Embed chunk texts in batches; `on_batch(done, total)` reports progress."""
        vectors: list[list[float]] = []
        for start in range(0, len(texts), self.batch_size):
            batch = [self.doc_prefix + t for t in texts[start : start + self.batch_size]]
            vectors.extend(self._embed(batch))
            if on_batch:
                on_batch(len(vectors), len(texts))
        return vectors

    def embed_query(self, text: str) -> list[float]:
        return self._embed([self.query_prefix + text])[0]

    def embed_queries(self, texts: Sequence[str]) -> list[list[float]]:
        """Batch of questions, embedded exactly like embed_query (router training)."""
        vectors: list[list[float]] = []
        for start in range(0, len(texts), self.batch_size):
            vectors.extend(self._embed([self.query_prefix + t for t in texts[start : start + self.batch_size]]))
        return vectors

    def _embed(self, texts: list[str]) -> list[list[float]]:
        response = self._client.post(self.url, json={"model": self.model, "input": texts})
        response.raise_for_status()
        data = response.json().get("data")
        if not isinstance(data, list) or len(data) != len(texts):
            raise EmbeddingError("Unexpected response from the embedding server.")
        vectors = [item["embedding"] for item in sorted(data, key=lambda item: item["index"])]
        if any(len(v) != self.dim for v in vectors):
            raise EmbeddingError(
                f"Embedding server returned {len(vectors[0])}-dim vectors; ERINNE_EMBED_DIM is {self.dim}."
            )
        return vectors

    def close(self) -> None:
        self._client.close()


def pack_vector(vector: Sequence[float]) -> bytes:
    """sqlite-vec's float32 BLOB format."""
    return struct.pack(f"{len(vector)}f", *vector)
