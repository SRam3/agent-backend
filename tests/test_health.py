from fastapi.testclient import TestClient
from unittest import mock
import sys
from pathlib import Path
import importlib

# Ensure the sales_agent_api package is on the path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def test_health_endpoint(monkeypatch):
    """Verify the health endpoint without hitting Azure."""

    monkeypatch.setenv("KEY_VAULT_URL", "https://fake.vault.azure.net/")

    class DummySecret:
        def __init__(self, value):
            self.value = value

    def fake_get_secret(name):
        mapping = {
            "DBUSERNAME": DummySecret("user"),
            "DBPASSWORD": DummySecret("pass"), 
            "DBHOST": DummySecret("localhost"),
            "DBNAME": DummySecret("testdb"),
        }
        return mapping[name]

    with mock.patch("azure.keyvault.secrets.SecretClient") as sc, mock.patch(
        "azure.identity.DefaultAzureCredential"
    ):
        sc.return_value.get_secret.side_effect = fake_get_secret

        import sales_agent_api.app.db as db
        importlib.reload(db)

        from sales_agent_api.app.main import app

        client = TestClient(app)
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json() == {
            "status": "ok",
            "message": "Backend reachable by LLM",
        }


def test_health_endpoint_local_env(monkeypatch):
    """Verify the health endpoint using local environment variables."""

    monkeypatch.delenv("KEY_VAULT_URL", raising=False)
    monkeypatch.setenv("DBUSERNAME", "user")
    monkeypatch.setenv("DBPASSWORD", "pass")
    monkeypatch.setenv("DBHOST", "localhost")
    monkeypatch.setenv("DBNAME", "testdb")

    import sales_agent_api.app.db as db
    import importlib
    importlib.reload(db)

    from sales_agent_api.app.main import app

    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "message": "Backend reachable by LLM",
    }


def test_health_endpoint_dotenv(monkeypatch):
    """Verify credentials are loaded from the packaged .env file."""

    monkeypatch.delenv("KEY_VAULT_URL", raising=False)
    monkeypatch.delenv("DBUSERNAME", raising=False)
    monkeypatch.delenv("DBPASSWORD", raising=False)
    monkeypatch.delenv("DBHOST", raising=False)
    monkeypatch.delenv("DBNAME", raising=False)

    import sales_agent_api.app.db as db
    import importlib
    importlib.reload(db)

    from sales_agent_api.app.main import app

    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "message": "Backend reachable by LLM",
    }
