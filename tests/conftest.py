import pytest
from fastapi.testclient import TestClient

from mnemerynne import db, share
from mnemerynne.config import Settings
from mnemerynne.web.app import create_app


@pytest.fixture(autouse=True)
def _no_share():
    # share.configure() is process-wide: never let one test's share leak into the next.
    share.configure(Settings(_env_file=None), None)
    yield
    share.configure(Settings(_env_file=None), None)


class _NoAgentModel:
    """Stands in for the agent's model where a test doesn't give one (Services.agent_llm): never
    answers, so questions take the normal flow without trying the network."""

    async def reachable(self):
        return False

    async def chat_tools(self, *args, **kwargs):
        raise OSError("no agent model in tests")

    async def aclose(self):
        pass


@pytest.fixture(autouse=True)
def _no_agent_model(monkeypatch):
    # Everyone gets the agent now: without this, every chat test would try 127.0.0.1:8081.
    from mnemerynne import agent
    monkeypatch.setattr(agent.Agent, "_default_client", lambda self, cfg: _NoAgentModel())


@pytest.fixture
def settings(tmp_path):
    # Small vectors keep tests fast; production uses the embedder's real size.
    # _env_file=None: never pick up the developer's .env (API keys, teacher settings).
    return Settings(_env_file=None, data_dir=tmp_path / "data", secret_key="test-secret-key-for-the-test-suite-only", embed_dim=4)


@pytest.fixture
def conn(settings):
    connection = db.connect(settings.db_path)
    db.migrate(connection, settings.embed_dim)
    yield connection
    connection.close()


@pytest.fixture
def client(settings, conn):
    with TestClient(create_app(settings)) as test_client:
        yield test_client
