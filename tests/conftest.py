import pytest
from fastapi.testclient import TestClient

from erinne import db
from erinne.config import Settings
from erinne.web.app import create_app


@pytest.fixture
def settings(tmp_path):
    # Small vectors keep tests fast; production uses the embedder's real size.
    # _env_file=None: never pick up the developer's .env (API keys, teacher settings).
    return Settings(_env_file=None, data_dir=tmp_path / "data", secret_key="test-secret", embed_dim=4)


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
