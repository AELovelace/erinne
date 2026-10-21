"""Reranker client for llama-server started with --reranking (/v1/rerank)."""

from __future__ import annotations

import httpx


class Reranker:
    def __init__(self, base_url: str, timeout: float = 30.0, client: httpx.Client | None = None) -> None:
        self.url = base_url.rstrip("/") + "/v1/rerank"
        self._client = client or httpx.Client(timeout=timeout)

    def scores(self, query: str, documents: list[str]) -> list[float]:
        """Relevance score per document, in the input order."""
        if not documents:
            return []
        response = self._client.post(self.url, json={"query": query, "documents": documents})
        response.raise_for_status()
        out = [0.0] * len(documents)
        for item in response.json()["results"]:
            out[item["index"]] = float(item["relevance_score"])
        return out

    def close(self) -> None:
        self._client.close()
