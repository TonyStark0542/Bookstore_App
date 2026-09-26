import os
import sys
import pathlib
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

os.environ["SECRET_KEY"] = "test-secret-key"
os.environ["ADMIN_PASSWORD"] = "test-admin-password"
os.environ["INTERNAL_SERVICE_TOKEN"] = "test-internal-token"
os.environ["CATALOG_API_URL"] = "http://backend.test"


def fake_response(status_code=200, json_data=None):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = {} if json_data is None else json_data
    return resp


@pytest.fixture
def app_module():
    """Fresh import of app_frontend per test — its module-level backend_session
    is recreated each time, so mocking it never leaks between tests."""
    sys.modules.pop("app_frontend", None)
    import app_frontend
    yield app_frontend
    sys.modules.pop("app_frontend", None)


@pytest.fixture
def client(app_module):
    app_module.app.config.update(TESTING=True)
    return app_module.app.test_client()


@pytest.fixture
def mock_backend(app_module, monkeypatch):
    """Replace every HTTP verb on the shared backend_session with a mock
    that returns 200/{} by default — override .return_value per test."""
    mocks = {
        "get": MagicMock(return_value=fake_response()),
        "post": MagicMock(return_value=fake_response()),
        "put": MagicMock(return_value=fake_response()),
        "delete": MagicMock(return_value=fake_response()),
    }
    for verb, mock in mocks.items():
        monkeypatch.setattr(app_module.backend_session, verb, mock)
    return mocks


@pytest.fixture
def logged_in_client(client):
    with client.session_transaction() as sess:
        sess["user_id"] = "user123"
        sess["user_name"] = "Test User"
    return client


@pytest.fixture
def admin_client(client):
    with client.session_transaction() as sess:
        sess["is_admin"] = True
    return client
