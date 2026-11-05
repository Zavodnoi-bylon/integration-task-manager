from __future__ import annotations

import argparse
import hashlib
import os
import re
from pathlib import Path

MIGRATION_RE = re.compile(r"^(?P<version>[0-9]{4})_[a-z0-9_]+\.sql$")
LOCK_ID = 814211970261030
ROOT = Path(__file__).resolve().parents[1]


def discover(directory: Path) -> list[tuple[int, Path, str]]:
    result = []
    seen = set()
    for path in sorted(directory.glob("*.sql")):
        match = MIGRATION_RE.fullmatch(path.name)
        if match is None:
            raise RuntimeError(f"Invalid migration filename: {path.name}")
        version = int(match.group("version"))
        if version in seen:
            raise RuntimeError(f"Duplicate migration version: {version:04d}")
        seen.add(version)
        result.append((version, path, hashlib.sha256(path.read_bytes()).hexdigest()))
    if not result:
        raise RuntimeError("No migrations found")
    return result


def apply(dsn: str, directory: Path) -> list[str]:
    import psycopg
    from psycopg import ClientCursor

    applied = []
    with psycopg.connect(dsn) as connection:
        with connection.transaction():
            connection.execute("SELECT pg_advisory_xact_lock(%s)", (LOCK_ID,))
            if connection.execute(
                "SELECT to_regclass('ticketing.tickets')"
            ).fetchone()[0] is None:
                raise RuntimeError("Canonical ticketing schema 0001 is required")
            connection.execute(
                """CREATE TABLE IF NOT EXISTS ticketing.schema_migrations (
                   version integer PRIMARY KEY CHECK (version > 0),
                   filename text NOT NULL UNIQUE,sha256 char(64) NOT NULL,
                   applied_at timestamptz NOT NULL DEFAULT now())"""
            )
            existing = {
                int(row[0]): (str(row[1]), str(row[2]))
                for row in connection.execute(
                    "SELECT version,filename,sha256 FROM ticketing.schema_migrations"
                ).fetchall()
            }
            for version, path, digest in discover(directory):
                recorded = existing.get(version)
                if recorded:
                    if recorded != (path.name, digest):
                        raise RuntimeError(f"Applied migration was modified: {path.name}")
                    continue
                with ClientCursor(connection) as cursor:
                    cursor.execute(path.read_text(encoding="utf-8"))
                connection.execute(
                    "INSERT INTO ticketing.schema_migrations(version,filename,sha256) "
                    "VALUES (%s,%s,%s)", (version, path.name, digest)
                )
                applied.append(path.name)
    return applied


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--directory", type=Path, default=ROOT / "migrations")
    parser.add_argument("--dsn-env", default="DATABASE_MIGRATION_URL")
    args = parser.parse_args()
    dsn = os.getenv(args.dsn_env, "").strip()
    if not dsn:
        raise SystemExit(f"{args.dsn_env} is required")
    applied = apply(dsn, args.directory.resolve())
    print("Applied: " + ", ".join(applied) if applied else "Schema is up to date")


if __name__ == "__main__":
    main()
