-- Lattice production bootstrap (Phase 11, P11.2; docs/deployment.md §7). Safe to re-run.
--
-- For a managed PostgreSQL, which gives no superuser: run once (and again to rotate the
-- passwords) by the platform's administrative role. That role needs CREATEROLE and
-- CREATEDB -- nothing more (no SUPERUSER, no BYPASSRLS). Differences from bootstrap.sql
-- (development and CI, run as a superuser):
--
--   * no test database;
--   * no ALTER ROLE ... NOSUPERUSER/NOBYPASSRLS/NOREPLICATION: PostgreSQL 16 lets only a
--     role holding an attribute change it. New roles already get the safe defaults, and
--     step 4 fails the bootstrap if any Lattice role carries an unsafe attribute (fix it
--     with the platform, never by running the application anyway);
--   * PostgreSQL 16 lets a CREATEDB role create a database for another owner only if it
--     may SET ROLE to that owner, and only the owner can connect once PUBLIC's CONNECT is
--     revoked: the administrator holds a membership in lattice_owner for the bootstrap
--     only (step 2) and gives it up at the end of step 3;
--   * citext (a trusted extension) and the database grants are made as the database
--     owner. pgvector is not a trusted extension: only a superuser -- or the platform's
--     own extension mechanism acting for its administrative role -- can create it, so it
--     is created as the administrator, and on a platform without that mechanism it must
--     be enabled through the platform first (CREATE EXTENSION IF NOT EXISTS then skips).
--
-- Usage:
--   psql "host=... user=<admin> dbname=postgres sslmode=require" \
--     -v owner_password=... -v app_password=... -v worker_password=... \
--     -f bootstrap_production.sql
--
-- The roles and their purpose are documented in bootstrap.sql; the startup checks
-- (lattice check-db-role, lattice worker --once) and `lattice check-deployment` remain the
-- authority on whether the result is safe.

\set ON_ERROR_STOP on

-- 1. Roles. CREATE ROLE defaults: NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS
--    NOREPLICATION. The creator receives ADMIN OPTION on each (PostgreSQL 16).
SELECT format('CREATE ROLE lattice_owner LOGIN PASSWORD %L', :'owner_password')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'lattice_owner') \gexec
SELECT format('CREATE ROLE lattice_app LOGIN PASSWORD %L', :'app_password')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'lattice_app') \gexec
SELECT format('CREATE ROLE lattice_worker LOGIN PASSWORD %L', :'worker_password')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'lattice_worker') \gexec
SELECT 'CREATE ROLE lattice_auth NOLOGIN'
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'lattice_auth') \gexec

-- Re-asserted on every run, so a rotated password takes effect.
SELECT format('ALTER ROLE lattice_owner LOGIN PASSWORD %L', :'owner_password') \gexec
SELECT format('ALTER ROLE lattice_app LOGIN PASSWORD %L', :'app_password') \gexec
SELECT format('ALTER ROLE lattice_worker LOGIN PASSWORD %L', :'worker_password') \gexec

-- As bootstrap.sql: migrations hand function ownership to lattice_auth; INHERIT FALSE
-- keeps lattice_auth's role-targeted policies away from the owner.
GRANT lattice_auth TO lattice_owner WITH INHERIT FALSE, SET TRUE;

-- 2. The database, owned by lattice_owner.
GRANT lattice_owner TO CURRENT_USER WITH INHERIT TRUE, SET TRUE;
SELECT 'CREATE DATABASE lattice OWNER lattice_owner'
WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = 'lattice') \gexec

-- 3. Extensions and grants, as the database owner (bootstrap_database.sql's content).
\connect lattice
CREATE EXTENSION IF NOT EXISTS vector;
SET ROLE lattice_owner;
CREATE EXTENSION IF NOT EXISTS citext;
REVOKE ALL ON DATABASE lattice FROM PUBLIC;
GRANT CONNECT, TEMPORARY ON DATABASE lattice TO lattice_owner;
GRANT CONNECT ON DATABASE lattice TO lattice_app, lattice_worker;
ALTER SCHEMA public OWNER TO lattice_owner;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO lattice_app, lattice_worker, lattice_auth;
REVOKE CREATE ON SCHEMA public FROM lattice_auth;
RESET ROLE;
REVOKE lattice_owner FROM CURRENT_USER;

-- 4. Fail the bootstrap if a Lattice role is unsafe (an attribute set by the platform
--    or left from an earlier, hand-made setup).
DO $$
DECLARE
  unsafe text;
BEGIN
  SELECT string_agg(rolname, ', ' ORDER BY rolname) INTO unsafe FROM pg_roles
  WHERE rolname IN ('lattice_owner', 'lattice_app', 'lattice_worker', 'lattice_auth')
    AND (rolsuper OR rolbypassrls OR rolcreaterole OR rolcreatedb OR rolreplication);
  IF unsafe IS NOT NULL THEN
    RAISE EXCEPTION 'unsafe role attribute (SUPERUSER, BYPASSRLS, CREATEROLE, CREATEDB or REPLICATION) on: %', unsafe;
  END IF;
  IF (SELECT rolcanlogin FROM pg_roles WHERE rolname = 'lattice_auth') THEN
    RAISE EXCEPTION 'lattice_auth must be NOLOGIN';
  END IF;
  SELECT string_agg(m.rolname || ' in ' || g.rolname, ', ') INTO unsafe
  FROM pg_auth_members am JOIN pg_roles m ON m.oid = am.member JOIN pg_roles g ON g.oid = am.roleid
  WHERE m.rolname IN ('lattice_app', 'lattice_worker');
  IF unsafe IS NOT NULL THEN
    RAISE EXCEPTION 'runtime roles must not be members of any role: %', unsafe;
  END IF;
  IF (SELECT count(*) FROM pg_extension WHERE extname IN ('citext', 'vector')) <> 2 THEN
    RAISE EXCEPTION 'extensions citext and vector are required';
  END IF;
  IF (SELECT string_to_array(extversion, '.')::int[] FROM pg_extension
      WHERE extname = 'vector') < ARRAY[0, 8] THEN
    RAISE EXCEPTION 'pgvector 0.8 or later is required';
  END IF;
END
$$;

SELECT 'lattice bootstrap complete: roles, database lattice, extensions, grants' AS result;
