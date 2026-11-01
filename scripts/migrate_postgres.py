"""Apply immutable PostgreSQL migrations with checksum verification."""
from __future__ import annotations

import argparse
import hashlib
import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DIRECTORY = ROOT / "migrations"
MIGRATION_NAME = re.compile(r"^(?P<version>[0-9]{4})_[a-z0-9_]+\.sql$")
ADVISORY_LOCK_ID = 814211970261030


def discover_migrations(directory: Path) -> list[tuple[int, Path, str]]:
    migrations: list[tuple[int, Path, str]] = []
    versions: set[int] = set()
    for path in sorted(directory.glob("*.sql")):
        match = MIGRATION_NAME.fullmatch(path.name)
        if match is None:
            raise RuntimeError(f"Invalid migration filename: {path.name}")
        version = int(match.group("version"))
        if version in versions:
            raise RuntimeError(f"Duplicate migration version: {version:04d}")
        versions.add(version)
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        migrations.append((version, path, digest))
    if not migrations:
        raise RuntimeError(f"No migrations found in {directory}")
    return migrations


def apply_migrations(dsn: str, directory: Path = DEFAULT_DIRECTORY) -> list[str]:
    import psycopg
    from psycopg import ClientCursor

    migrations = discover_migrations(directory)
    applied_now: list[str] = []
    with psycopg.connect(dsn) as connection:
        with connection.transaction():
            connection.execute("SELECT pg_advisory_xact_lock(%s)", (ADVISORY_LOCK_ID,))
            connection.execute("CREATE SCHEMA IF NOT EXISTS ticketing")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS ticketing.schema_migrations (
                    version integer PRIMARY KEY CHECK (version > 0),
                    filename text NOT NULL UNIQUE,
                    sha256 char(64) NOT NULL,
                    applied_at timestamptz NOT NULL DEFAULT now()
                )
                """
            )
            existing = {
                int(row[0]): (str(row[1]), str(row[2]))
                for row in connection.execute(
                    "SELECT version, filename, sha256 "
                    "FROM ticketing.schema_migrations ORDER BY version"
                ).fetchall()
            }
            for version, path, digest in migrations:
                recorded = existing.get(version)
                if recorded is not None:
                    if recorded != (path.name, digest):
                        raise RuntimeError(
                            f"Applied migration {version:04d} was modified: {path.name}"
                        )
                    continue
                # ClientCursor uses PostgreSQL's simple-query protocol, which is
                # required for a migration file containing multiple statements
                # and dollar-quoted PL/pgSQL function bodies.
                with ClientCursor(connection) as cursor:
                    cursor.execute(path.read_text(encoding="utf-8"))
                connection.execute(
                    "INSERT INTO ticketing.schema_migrations "
                    "(version, filename, sha256) VALUES (%s, %s, %s)",
                    (version, path.name, digest),
                )
                applied_now.append(path.name)
    return applied_now


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--directory",
        type=Path,
        default=DEFAULT_DIRECTORY,
        help="Directory containing numbered SQL migrations",
    )
    parser.add_argument(
        "--dsn-env",
        default="DATABASE_MIGRATION_URL",
        help="Environment variable containing an administrator DSN",
    )
    args = parser.parse_args()
    dsn = os.getenv(args.dsn_env, "").strip()
    if not dsn:
        raise SystemExit(f"{args.dsn_env} is required")
    applied = apply_migrations(dsn, args.directory.resolve())
    if applied:
        print("Applied migrations: " + ", ".join(applied))
    else:
        print("Database schema is up to date")


if __name__ == "__main__":
    main()
