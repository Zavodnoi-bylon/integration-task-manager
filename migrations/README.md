# PostgreSQL migrations

Files are applied in numeric order by `scripts/migrate_postgres.py`. Applied
files are immutable: the runner stores a SHA-256 checksum in
`ticketing.schema_migrations` and refuses a changed migration.

Run migrations with a PostgreSQL administrator account. The bot's
`ticketing_writer` account intentionally cannot change the schema.

```bash
export DATABASE_MIGRATION_URL='postgresql://admin:password@host/database'
python scripts/migrate_postgres.py
```

Do not commit production DSNs, dumps, ticket data, Telegram IDs, or passwords.

For a clean-database verification, create a disposable database whose name starts
with `integration_task_manager_verify_` and run:

```bash
DATABASE_MIGRATION_URL='postgresql://admin:password@host/integration_task_manager_verify_1' \
  python scripts/verify_migrations.py
```

The same disposable database can be used to exercise the application repository:

```bash
DATABASE_BACKEND=postgres TICKETING_DB_NAME=integration_task_manager_verify_1 \
  python scripts/verify_repository.py
```
