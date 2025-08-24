from fastapi.testclient import TestClient
import importlib
import dotenv


def test_register_user_sqlite_fallback(monkeypatch, tmp_path):
    # Ensure no database credentials are set so the app falls back to SQLite
    for var in ["KEY_VAULT_URL", "DBUSERNAME", "DBPASSWORD", "DBHOST", "DBNAME"]:
        monkeypatch.delenv(var, raising=False)

    # Prevent loading of the repository .env file which may contain DB settings
    monkeypatch.setattr(dotenv, "load_dotenv", lambda *args, **kwargs: False)

    # Run in a temporary directory so the SQLite file is isolated
    monkeypatch.chdir(tmp_path)

    import sales_agent_api.app.db as db
    importlib.reload(db)
    import sales_agent_api.app.main as main
    importlib.reload(main)

    with TestClient(main.app) as client:
        payload = {"name": "Alice", "phone": "123"}
        response = client.post("/users/register", json=payload)
        assert response.status_code == 201
        assert response.json()["message"] == "User registered successfully"
