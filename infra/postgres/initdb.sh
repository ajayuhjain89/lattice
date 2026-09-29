#!/bin/sh
# Runs once, as the Postgres superuser, when the data volume is first initialized.
set -eu
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres \
  -v owner_password="$LATTICE_OWNER_PASSWORD" \
  -v app_password="$LATTICE_APP_PASSWORD" \
  -v worker_password="$LATTICE_WORKER_PASSWORD" \
  -f /bootstrap/bootstrap.sql
