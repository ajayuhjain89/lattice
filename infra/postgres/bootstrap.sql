-- Lattice database bootstrap. Must run as a superuser; safe to re-run.
--
-- Privileged, cluster-level setup lives here rather than in Alembic migrations:
-- creating login roles needs their passwords (secrets) and CREATEROLE, and creating
-- extensions needs superuser. Migrations run as lattice_owner and never hold either.
--
-- Usage: psql -v owner_password=... -v app_password=... -f bootstrap.sql
--
-- Roles:
--   lattice_owner  owns the schema; runs migrations and operator tools (seed).
--                  Subject to FORCE ROW LEVEL SECURITY on tenant tables.
--   lattice_app    runtime role for the API and worker. Never owns tables, never
--                  BYPASSRLS; the API refuses to start otherwise.
--   lattice_auth   NOLOGIN. Owns the narrow SECURITY DEFINER pre-authentication
--                  lookup functions; has role-targeted read policies. Nobody logs
--                  in as it and lattice_app is not a member of it. It holds no
--                  CREATE privilege on any schema or database at rest.

\set ON_ERROR_STOP on

SELECT format('CREATE ROLE lattice_owner LOGIN PASSWORD %L', :'owner_password')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'lattice_owner') \gexec
SELECT format('CREATE ROLE lattice_app LOGIN PASSWORD %L', :'app_password')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'lattice_app') \gexec
SELECT 'CREATE ROLE lattice_auth NOLOGIN'
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'lattice_auth') \gexec

-- Re-assert attributes and passwords on every run so drift is corrected.
SELECT format('ALTER ROLE lattice_owner LOGIN PASSWORD %L', :'owner_password') \gexec
SELECT format('ALTER ROLE lattice_app LOGIN PASSWORD %L', :'app_password') \gexec
ALTER ROLE lattice_owner NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS NOREPLICATION;
ALTER ROLE lattice_app   NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS NOREPLICATION;
ALTER ROLE lattice_auth  NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS NOREPLICATION;

-- lattice_owner must be able to hand function ownership to lattice_auth in migrations
-- (ALTER FUNCTION ... OWNER TO requires being able to SET ROLE to the new owner).
-- INHERIT FALSE: the owner does not silently acquire lattice_auth's role-targeted
-- RLS policies, so tenant tables stay FORCE-RLS-constrained for the owner too.
GRANT lattice_auth TO lattice_owner WITH INHERIT FALSE, SET TRUE;

SELECT 'CREATE DATABASE lattice OWNER lattice_owner'
WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = 'lattice') \gexec
SELECT 'CREATE DATABASE lattice_test OWNER lattice_owner'
WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = 'lattice_test') \gexec

\connect lattice
\ir bootstrap_database.sql
\connect lattice_test
\ir bootstrap_database.sql
