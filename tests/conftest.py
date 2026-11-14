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


class FeatureConn:
    """A feature's tables for a test, wherever they are at the moment of each call: core's own connection until the
    app (db.migrate_all at its start) has moved them, the feature's file after. Tests write some rows before
    starting the app and read others after, so the choice is made per call. Stands in for a sqlite3.Connection."""

    def __init__(self, core, feature: str, table: str) -> None:
        self._core, self._feature, self._table = core, feature, table
        self._own = None

    def _target(self):
        if self._core.execute("SELECT 1 FROM main.sqlite_master WHERE type = 'table' AND name = ?",
                              (self._table,)).fetchone():
            return self._core
        if self._own is None:
            core_path = self._core.execute("PRAGMA database_list").fetchone()[2]
            self._own = db.connect_feature(core_path, self._feature)
        return self._own

    def __getattr__(self, name):
        return getattr(self._target(), name)

    def close_own(self) -> None:
        if self._own is not None:
            self._own.close()


@pytest.fixture
def media_conn(conn):
    """Media's tables (media.db once the app has started; Phase 4), for tests that read or write them directly."""
    found = FeatureConn(conn, "media", "media_items")
    yield found
    found.close_own()


@pytest.fixture
def games_conn(settings, conn):
    """games.db as the app has it once it started: db.migrate_all moved the games' tables out of core there
    (docs/plans/services-rewrite.md, Phase 2). Core's tables (users...) are still readable through it."""
    db.migrate_all(settings.db_path, settings.embed_dim)
    connection = db.connect_feature(settings.db_path, "games")
    yield connection
    connection.close()


@pytest.fixture
def client(settings, conn):
    with TestClient(create_app(settings)) as test_client:
        yield test_client
