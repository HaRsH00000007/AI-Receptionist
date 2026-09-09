-- Databases created on first boot of an empty Postgres data directory.
--
-- Postgres runs /docker-entrypoint-initdb.d exactly once, when the data volume
-- is empty. Re-running the stack does not re-run this file, so it is not
-- written to be idempotent -- `docker compose down -v` is what replays it.

-- The scratch database the backend test suite drops and recreates per session.
-- It must exist for the *maintenance* connection to succeed; the suite owns its
-- lifecycle from there.
CREATE DATABASE receptionist_test;

-- Temporal's own schemas. Separate databases, not separate schemas in the
-- application database: Temporal owns its migrations, and sharing a namespace
-- would make `alembic` autogenerate try to manage tables it does not own.
CREATE DATABASE temporal;
CREATE DATABASE temporal_visibility;
