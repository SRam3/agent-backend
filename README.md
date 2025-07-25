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

If `KEY_VAULT_URL` is **not** provided, credentials must instead come from the
following environment variables:

- `DB_USERNAME`
- `DB_PASSWORD`
- `DB_HOST`
- `DB_NAME`

The `.env` file in `sales_agent_api/` shows both approaches. Replace the values
as needed:

```dotenv
# KEY_VAULT_URL=https://<your-keyvault-name>.vault.azure.net/
DB_USERNAME=your-user
DB_PASSWORD=your-pass
DB_HOST=localhost
DB_NAME=testdb
```

## API Endpoints

- `GET /` – Welcome message.
- `GET /health` – Returns a simple payload confirming the backend is reachable.

