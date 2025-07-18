# Sales Agent API

This project contains a minimal FastAPI application used for LLM experiments.

## Development

Install dependencies:

```bash
pip install -r sales_agent_api/requirements.txt
```

Run tests:

```bash
pytest -q
```

Database credentials are always fetched from **Azure Key Vault**. Set the
`KEY_VAULT_URL` environment variable to the URL of your vault. The vault must
contain the following secrets:

- `DBUSERNAME`
- `psqladmin-password`
- `DBHOST`
- `DBNAME`

The `.env` file in `sales_agent_api/` shows how to provide the vault URL during
development.

## API Endpoints

- `GET /` – Welcome message.
- `GET /health` – Returns a simple payload confirming the backend is reachable.
