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
            "psqladmin-password": DummySecret("pass"),
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


