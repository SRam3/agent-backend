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

Database credentials are always fetched from **Azure Key Vault**. Set the
`KEY_VAULT_URL` environment variable to your vault URL, e.g.
`https://kv-r8fm.vault.azure.net/`. The vault must
contain the following secrets:

- `DBUSERNAME`
- `psqladmin-password`
- `DBHOST`
- `DBNAME`

The `.env` file in `sales_agent_api/` shows how to provide the vault URL during
development. It contains a single line specifying your Key Vault. Replace the
value if your vault URL differs:

```dotenv
KEY_VAULT_URL=https://kv-r8fm.vault.azure.net/
```

## API Endpoints

- `GET /` – Welcome message.
- `GET /health` – Returns a simple payload confirming the backend is reachable.
