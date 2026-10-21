"""Message router: which flow should handle a message?

A multinomial logistic regression over the question's embedding (the same
Qwen3-Embedding vector search uses), so routing costs a dot product, not a
model call. Routes are open-ended: the model learns whatever labels its
training data has ("search", "smalltalk", "general", later more).

Cautious by design: a message leaves "search" only when another route is
clearly more likely (probability >= its threshold). Sending a document
question to chat loses a grounded answer; sending chat to search only costs
a "not found".
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

log = logging.getLogger("mnemerynne.router")

DEFAULT_ROUTE = "search"
DEFAULT_THRESHOLD = 0.85


class RouterError(RuntimeError):
    pass


@dataclass
class Decision:
    route: str
    probabilities: dict[str, float]


@dataclass
class Router:
    labels: list[str]
    weights: np.ndarray            # (dim, n_labels)
    bias: np.ndarray               # (n_labels,)
    embed_model: str
    thresholds: dict[str, float] = field(default_factory=dict)
    metrics: dict = field(default_factory=dict)
    trained_at: str = ""
    # Feature standardisation learned at training time. Embeddings are
    # anisotropic (all vectors share a large common direction), so centring and
    # scaling each dimension is what lets a linear model become confident.
    mean: np.ndarray | None = None
    std: np.ndarray | None = None

    def _features(self, x: np.ndarray) -> np.ndarray:
        x = _normalise(x)
        if self.mean is not None:
            x = (x - self.mean) / self.std
        return x

    def probabilities(self, vector) -> np.ndarray:
        x = self._features(np.asarray(vector, dtype=np.float32)[None, :])
        return _softmax(x @ self.weights + self.bias)[0]

    def decide(self, vector) -> Decision:
        probs = self.probabilities(vector)
        by_label = {label: round(float(p), 4) for label, p in zip(self.labels, probs)}
        best = max(by_label, key=by_label.get)
        if best != DEFAULT_ROUTE and by_label[best] >= self.thresholds.get(best, DEFAULT_THRESHOLD):
            return Decision(best, by_label)
        return Decision(DEFAULT_ROUTE, by_label)

    # ----------------------------------------------------------- persistence
    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "labels": self.labels, "embed_model": self.embed_model, "thresholds": self.thresholds,
            "metrics": self.metrics, "trained_at": self.trained_at,
            "weights": self.weights.round(6).tolist(), "bias": self.bias.round(6).tolist(),
            "mean": None if self.mean is None else self.mean.round(6).tolist(),
            "std": None if self.std is None else self.std.round(6).tolist(),
        }), encoding="utf-8")

    @classmethod
    def load(cls, path: Path, embed_model: str, dim: int) -> "Router":
        raw = json.loads(path.read_text(encoding="utf-8"))
        router = cls(raw["labels"], np.asarray(raw["weights"], dtype=np.float32),
                     np.asarray(raw["bias"], dtype=np.float32), raw["embed_model"],
                     raw.get("thresholds", {}), raw.get("metrics", {}), raw.get("trained_at", ""),
                     None if raw.get("mean") is None else np.asarray(raw["mean"], dtype=np.float32),
                     None if raw.get("std") is None else np.asarray(raw["std"], dtype=np.float32))
        if router.embed_model != embed_model or router.weights.shape[0] != dim:
            raise RouterError(
                f"Router was trained on {router.embed_model} ({router.weights.shape[0]}-dim) but the embedder is "
                f"{embed_model} ({dim}-dim); retrain with `mnemerynne train-router`.")
        if DEFAULT_ROUTE not in router.labels:
            raise RouterError(f"Router has no '{DEFAULT_ROUTE}' label.")
        return router


def load_router(settings) -> Router | None:
    """The trained router, or None (everything goes to search) if there isn't one."""
    path = Path(settings.data_dir) / "router.json"
    if not path.exists():
        return None
    try:
        return Router.load(path, settings.embed_model, settings.embed_dim)
    except RouterError as exc:
        log.warning("%s Routing everything to search until then.", exc)
        return None


# ------------------------------------------------------------------ training
def _normalise(x: np.ndarray) -> np.ndarray:
    return x / np.maximum(np.linalg.norm(x, axis=1, keepdims=True), 1e-8)


def _softmax(z: np.ndarray) -> np.ndarray:
    z = z - z.max(axis=1, keepdims=True)
    e = np.exp(z)
    return e / e.sum(axis=1, keepdims=True)


def train(vectors, labels: list[str], *, embed_model: str, l2: float = 1e-2, epochs: int = 600,
          lr: float = 0.1, holdout: float = 0.2, seed: int = 7,
          thresholds: dict[str, float] | None = None) -> Router:
    """Fit the router; metrics are measured on a stratified held-out split, then
    the final model is refit on all the data."""
    x = _normalise(np.asarray(vectors, dtype=np.float32))
    names = sorted(set(labels))
    if DEFAULT_ROUTE not in names:
        raise RouterError(f"Training data needs '{DEFAULT_ROUTE}' examples.")
    y = np.array([names.index(label) for label in labels])

    rng = np.random.default_rng(seed)
    test_idx = np.concatenate([rng.permutation(np.where(y == k)[0])[: int(round(holdout * (y == k).sum()))]
                               for k in range(len(names))])
    train_mask = np.ones(len(y), dtype=bool)
    train_mask[test_idx] = False

    def standardise(rows):
        mean = rows.mean(axis=0)
        std = rows.std(axis=0) + 1e-6
        return mean, std

    mean, std = standardise(x[train_mask])
    w, b = _fit((x[train_mask] - mean) / std, y[train_mask], len(names), l2, epochs, lr)
    router = Router(names, w, b, embed_model, thresholds or {}, mean=mean, std=std)
    raw_test = np.asarray(vectors, dtype=np.float32)[~train_mask]
    router.metrics = evaluate(router, raw_test, y[~train_mask]) if len(test_idx) else {}
    router.mean, router.std = standardise(x)
    router.weights, router.bias = _fit((x - router.mean) / router.std, y, len(names), l2, epochs, lr)
    router.trained_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    router.metrics["examples"] = {name: int((y == k).sum()) for k, name in enumerate(names)}
    return router


def _fit(x: np.ndarray, y: np.ndarray, n_labels: int, l2: float, epochs: int, lr: float):
    n, dim = x.shape
    onehot = np.eye(n_labels, dtype=np.float32)[y]
    counts = np.bincount(y, minlength=n_labels).astype(np.float32)
    sample_w = (n / (n_labels * np.maximum(counts, 1)))[y][:, None]  # balanced classes
    w = np.zeros((dim, n_labels), dtype=np.float32)
    b = np.zeros(n_labels, dtype=np.float32)
    for _ in range(epochs):  # full-batch gradient descent; small data, convex problem
        grad = sample_w * (_softmax(x @ w + b) - onehot) / n
        w -= lr * (x.T @ grad + l2 * w)
        b -= lr * grad.sum(axis=0)
    return w, b


def evaluate(router: Router, x: np.ndarray, y: np.ndarray) -> dict:
    decided = [router.decide(v).route for v in x]
    truth = [router.labels[k] for k in y]
    out: dict = {"heldout": len(truth), "accuracy": round(np.mean([d == t for d, t in zip(decided, truth)]), 3)}
    for label in router.labels:
        tp = sum(d == label and t == label for d, t in zip(decided, truth))
        fp = sum(d == label and t != label for d, t in zip(decided, truth))
        fn = sum(d != label and t == label for d, t in zip(decided, truth))
        out[label] = {"precision": round(tp / (tp + fp), 3) if tp + fp else None,
                      "recall": round(tp / (tp + fn), 3) if tp + fn else None}
    # The costly mistake: document questions sent somewhere else.
    out["search_misrouted"] = sum(t == DEFAULT_ROUTE and d != DEFAULT_ROUTE for d, t in zip(decided, truth))
    return out
