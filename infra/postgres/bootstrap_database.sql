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
-- lattice_auth never holds CREATE at steady state. The migration that hands it the
-- SECURITY DEFINER lookup functions grants CREATE and revokes it again inside the
-- same transaction (a new owner must hold CREATE on the schema only at the moment of
-- ALTER ... OWNER TO). Re-running this bootstrap removes a grant left behind by an
-- older version of it.
REVOKE CREATE ON SCHEMA public FROM lattice_auth;
