-- Per-database part of the bootstrap (included by bootstrap.sql for each database).

CREATE EXTENSION IF NOT EXISTS citext;
CREATE EXTENSION IF NOT EXISTS vector;

REVOKE ALL ON DATABASE :"DBNAME" FROM PUBLIC;
GRANT CONNECT, TEMPORARY ON DATABASE :"DBNAME" TO lattice_owner;
GRANT CONNECT ON DATABASE :"DBNAME" TO lattice_app;

-- Only the owner creates objects; the runtime role can only use them.
ALTER SCHEMA public OWNER TO lattice_owner;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO lattice_app, lattice_auth;
-- Required for migrations to transfer ownership of the SECURITY DEFINER lookup
-- functions to lattice_auth (a new owner must hold CREATE on the function's schema).
-- lattice_auth is NOLOGIN and its functions never create objects.
GRANT CREATE ON SCHEMA public TO lattice_auth;
