#!/bin/sh
# Reference stack only (Phase 11, P11.2): make the local PostgreSQL look like a managed one.
#
# A managed platform keeps its superuser to itself and hands the customer an
# administrative role with CREATEROLE and CREATEDB. This creates exactly that role,
# `platform_admin` -- deliberately the weakest plausible one (no SUPERUSER, no BYPASSRLS,
# no REPLICATION) -- so the production bootstrap (infra/postgres/bootstrap_production.sql)
# is proved without a superuser. The superuser password stays inside this container.
#
# pgvector is not a trusted extension, so a non-superuser cannot create it. Managed
# platforms provide it through their own allow-list; here the stand-in is `template1`:
# every database created later (the bootstrap's `lattice`) already has it. This is an
# emulation, not evidence about any provider (deployment.md §7; D-22 amended).
# Runs once, when the data volume is first initialised.
set -eu
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres \
  -v admin_password="$PLATFORM_ADMIN_PASSWORD" <<'SQL'
SELECT format('CREATE ROLE platform_admin LOGIN CREATEROLE CREATEDB PASSWORD %L', :'admin_password')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'platform_admin') \gexec
SQL
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname template1 \
  -c 'CREATE EXTENSION IF NOT EXISTS vector'
