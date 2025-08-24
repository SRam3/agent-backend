# Sales Agent API

This project contains a minimal FastAPI application used for LLM experiments.

## Development

Install dependencies:

```bash
pip install -r sales_agent_api/requirements.txt
```

`httpx` is pinned below version 0.25 for compatibility with Starlette's
`TestClient` used in the tests.

Run tests:

```bash
pytest -q
```

Database credentials can be loaded from **Azure Key Vault** or directly from
environment variables. If the `KEY_VAULT_URL` variable is set, the application
will read secrets from your vault (e.g. `https://kv-r8fm.vault.azure.net/`). The
vault must contain the following secrets:

- `DBUSERNAME`
- `psqladmin-password`
- `DBHOST`
- `DBNAME`

If `KEY_VAULT_URL` is **not** provided, credentials can come from the
following environment variables. The application supports both the `.env` file
and system environment variables using the standardized Key Vault-compatible
names:
- `DBUSERNAME`
- `DBPASSWORD`
- `DBHOST`
- `DBNAME`

The `.env` file in `sales_agent_api/` is loaded automatically when the app
starts, so you can place your local credentials there. Replace the values as
needed:

```dotenv
# KEY_VAULT_URL=https://<your-keyvault-name>.vault.azure.net/
DBUSERNAME=your-user
DBPASSWORD=your-pass
DBHOST=localhost
DBNAME=testdb
```

If neither Key Vault secrets nor the environment variables above are provided,
the application falls back to a local SQLite database stored in
`./sales_agent.db`. This is convenient for quick development or testing
scenarios where running a full PostgreSQL instance is unnecessary.

## API Endpoints

- `GET /` – Welcome message.
- `GET /health` – Returns a simple payload confirming the backend is reachable.

## Docker

The repository's Dockerfile lives in the `sales_agent_api/` directory. Build
the image using that directory as the build context:

```bash
docker build -t sales-agent-api -f sales_agent_api/Dockerfile sales_agent_api
```

Running the container exposes the API on port 8000:

```bash
docker run -p 8000:8000 sales-agent-api
```

