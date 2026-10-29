"""Runtime-selected persistence adapter.

SQLite remains available for local tests. Production uses the canonical PostgreSQL
ticketing schema without changing the application service contract.
"""
import config

if config.DATABASE_BACKEND == "postgres":
    from app.postgres import *  # noqa: F403
elif config.DATABASE_BACKEND == "sqlite":
    from app.sql import *  # noqa: F403
else:
    raise RuntimeError(f"Unsupported DATABASE_BACKEND: {config.DATABASE_BACKEND}")
