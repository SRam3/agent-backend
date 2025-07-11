# agent-backend
This repository uses Alembic for database migrations. To set up the project loca
lly:

1. Create a `.env` file in the project root with the variable `DATABASE_URL` poi
   nting to your PostgreSQL instance. Example:

   ```ini
   DATABASE_URL=postgresql+psycopg2://user:password@localhost:5432/agent_db
   ```

2. Install dependencies (Alembic and Psycopg):

   ```bash
   pip install alembic psycopg2-binary
   ```

3. Run the migrations:

   ```bash
   alembic upgrade head
   ```

This will create the initial schema for clients, client users, conversations, m
essages and leads using auto-incrementing integer primary keys.

