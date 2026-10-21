import numpy as np
import pytest

from erinne import router as router_mod
from erinne.router import Router, RouterError, load_router, train


def _clusters(n=60, dim=16, seed=0):
    rng = np.random.default_rng(seed)
    centers = {"search": np.eye(dim)[0], "smalltalk": np.eye(dim)[1], "general": np.eye(dim)[2]}
    vectors, labels = [], []
    for label, c in centers.items():
        for _ in range(n):
            vectors.append(c + rng.normal(0, 0.15, dim))
            labels.append(label)
    return vectors, labels, centers


def test_train_separates_routes_and_reports_heldout_metrics():
    vectors, labels, centers = _clusters()
    model = train(vectors, labels, embed_model="m", thresholds={"smalltalk": .85, "general": .85})
    assert model.labels == ["general", "search", "smalltalk"]
    assert model.metrics["accuracy"] >= 0.95 and model.metrics["search_misrouted"] == 0
    assert model.metrics["examples"] == {"general": 60, "search": 60, "smalltalk": 60}
    assert model.decide(centers["smalltalk"]).route == "smalltalk"
    assert model.decide(centers["general"]).route == "general"
    assert model.decide(centers["search"]).route == "search"


def test_uncertain_messages_default_to_search():
    vectors, labels, centers = _clusters()
    model = train(vectors, labels, embed_model="m", thresholds={"smalltalk": .99, "general": .99})
    between = (centers["smalltalk"] + centers["general"]) / 2  # neither route is confident
    decision = model.decide(between)
    assert decision.route == "search"
    assert set(decision.probabilities) == {"general", "search", "smalltalk"}


def test_save_load_and_embedder_mismatch(tmp_path):
    vectors, labels, centers = _clusters()
    model = train(vectors, labels, embed_model="qwen3", thresholds={})
    path = tmp_path / "router.json"
    model.save(path)
    loaded = Router.load(path, "qwen3", 16)
    assert loaded.decide(centers["general"]).route == "general"
    with pytest.raises(RouterError, match="retrain"):
        Router.load(path, "jina", 16)
    with pytest.raises(RouterError, match="retrain"):
        Router.load(path, "qwen3", 1024)


def test_training_needs_search_examples():
    with pytest.raises(RouterError):
        train([[1.0, 0.0], [0.0, 1.0]], ["smalltalk", "general"], embed_model="m")


def test_load_router_missing_or_stale_returns_none(settings, tmp_path):
    assert load_router(settings) is None
    vectors, labels, _ = _clusters(dim=4)
    train(vectors, labels, embed_model="other-model").save(settings.data_dir / "router.json")
    assert load_router(settings) is None  # wrong embedder: logged, falls back to search
    train(vectors, labels, embed_model=settings.embed_model).save(settings.data_dir / "router.json")
    assert load_router(settings) is not None


def test_new_flows_fall_back_to_search_until_registered():
    from erinne.graph import FLOWS
    assert FLOWS["search"] == "retrieve" and FLOWS["general"] == "general" and FLOWS["smalltalk"] == "general"
    assert FLOWS.get("translate", "retrieve") == "retrieve"
    assert router_mod.DEFAULT_ROUTE == "search"
