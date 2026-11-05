#!/bin/sh
set -eu
# Docker's init hook supplies these variables. Never log their values.
: "${POSTGRES_USER:?required}"
: "${POSTGRES_DB:?required}"
: "${ACTIVITY_WRITER_PASSWORD:?required}"
: "${ACTIVITY_READER_PASSWORD:?required}"
psql --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" --set ON_ERROR_STOP=1 <<'SQL'
\getenv writer_password ACTIVITY_WRITER_PASSWORD
\getenv reader_password ACTIVITY_READER_PASSWORD
CREATE ROLE activity_writer LOGIN PASSWORD :'writer_password' NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION;
CREATE ROLE activity_reader LOGIN PASSWORD :'reader_password' NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION;
ALTER ROLE activity_reader SET default_transaction_read_only = on;
ALTER ROLE activity_reader SET statement_timeout = '15s';
ALTER ROLE activity_reader SET idle_in_transaction_session_timeout = '20s';
ALTER ROLE activity_writer SET statement_timeout = '120s';
ALTER ROLE activity_writer SET idle_in_transaction_session_timeout = '120s';
REVOKE ALL ON DATABASE activity FROM PUBLIC;
GRANT CONNECT ON DATABASE activity TO activity_reader, activity_writer;
SQL

