#!/usr/bin/env bash
# Backups and restores of the local reference stack (Phase 11, P11.5; deployment.md §11,
# runbook §7.7). LOCAL REFERENCE STACK ONLY: on a managed platform its own snapshots and
# PITR play this role (not verified here: deferred, phase-11-plan §21.1).
#
# The model is PostgreSQL's own: continuous WAL archiving (archive_timeout 60 s, so at most
# a minute of committed work is outside the archive) into the `pgarchive` volume, and
# physical base backups (pg_basebackup) into the `pgbackups` volume, both on the database
# container as the platform would; plus a row-count manifest per backup, taken as the
# schema owner (`lattice verify-restore --write-manifest`), kept in deploy/state/backups/.
#
# A restore never touches the live data: it copies a base backup into a new volume, replays
# the archive to the latest WAL or to --target-time (PITR), and starts that copy on an
# isolated network with no other service, where it is verified.
#
# Usage (from anywhere):
#   deploy/backup.sh backup                 base backup + manifest; prints its ID
#   deploy/backup.sh manifest NAME          a manifest of the live database now (+ WAL switch)
#   deploy/backup.sh list
#   deploy/backup.sh restore ID [--target-time ISO8601] [--manifest FILE]
#                                           stops the stack, restores into `restore-pgdata`,
#                                           verifies (verify-restore, check-db-role, worker
#                                           --once, an api instance's readiness, a secret
#                                           scan), prints the measured durations
#   deploy/backup.sh discard-restore        remove the restored copy and its network
# Exit codes: 0 ok; 1 failed (the message says where); 2 usage. Never prints a secret.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

REFERENCE_ENV=deploy/.env.reference
STATE=deploy/state/backups
IMAGES=deploy/state/images.env
PROJECT=lattice-reference

say() { printf '%s %s\n' "$(date -u +%H:%M:%S)" "$*"; }
fail() { say "FAILED: $*"; exit 1; }
usage() { sed -n '/^# Usage/,/^# Exit codes/p' "$0" >&2; exit 2; }
now() { python3 -c 'import time; print(f"{time.time():.3f}")'; }
since() { python3 -c "import sys; print(f'{float(sys.argv[2]) - float(sys.argv[1]):.1f}')" "$1" "$(now)"; }

compose() {
  local envs=(--env-file "$REFERENCE_ENV")
  [ -f "$IMAGES" ] && envs+=(--env-file "$IMAGES")
  docker compose "${envs[@]}" -f compose.production.yaml "$@"
}
psql_live() { compose exec -T -u postgres postgres psql -X -q -At -d lattice -c "$1"; }
ops() { compose run --rm --no-deps -T ops "$@"; }
backend_image() {
  local image
  image="$(compose config --images 2>/dev/null | grep -m1 backend || true)"
  echo "${image:-lattice-backend:local}"
}
pg_image() { compose config --images | grep -m1 pgvector; }
env_value() { sed -n "s/^$1=//p" "$REFERENCE_ENV" | tail -1; }

cmd_backup() {
  mkdir -p "$STATE"
  local id t0 seconds size
  id="base-$(date -u +%Y%m%dT%H%M%SZ)"
  say "backup $id: pg_basebackup (fast checkpoint, WAL streamed) into pgbackups"
  t0="$(now)"
  compose exec -T -u postgres postgres \
    pg_basebackup -D "/backups/$id" -Fp -Xs -c fast --no-password ||
    fail "pg_basebackup"
  psql_live "SELECT pg_switch_wal()" >/dev/null
  seconds="$(since "$t0")"
  size="$(compose exec -T -u postgres postgres du -sm "/backups/$id" | cut -f1)"
  ops verify-restore --write-manifest >"$STATE/$id.manifest.json" || fail "manifest"
  printf 'id=%s\nseconds=%s\nsize_mb=%s\nlabel_end=%s\n' "$id" "$seconds" "$size" \
    "$(psql_live "SELECT now()")" >"$STATE/$id.env"
  say "backup $id done in ${seconds} s (${size} MB); manifest $STATE/$id.manifest.json"
  echo "$id"
}

cmd_manifest() {
  [ $# -eq 1 ] || usage
  mkdir -p "$STATE"
  ops verify-restore --write-manifest >"$STATE/$1.manifest.json" || fail "manifest"
  psql_live "SELECT pg_switch_wal()" >/dev/null
  say "manifest $STATE/$1.manifest.json ($(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["taken_at"])' "$STATE/$1.manifest.json"))"
}

cmd_list() {
  compose exec -T -u postgres postgres sh -c 'ls -1 /backups 2>/dev/null; echo "archive: $(ls /archive | wc -l) WAL files"'
  ls -1 "$STATE" 2>/dev/null || true
}

restore_net="${PROJECT}_restore"
restore_volume="${PROJECT}_restore-pgdata"

cmd_discard() {
  docker rm -f lattice-restore-db lattice-restore-api >/dev/null 2>&1 || true
  docker volume rm "$restore_volume" >/dev/null 2>&1 || true
  docker network rm "$restore_net" >/dev/null 2>&1 || true
  say "restored copy discarded"
}

cmd_restore() {
  [ $# -ge 1 ] || usage
  local id="$1" target="" manifest=""
  shift
  while [ $# -gt 0 ]; do
    case "$1" in
      --target-time) target="$2"; shift 2 ;;
      --manifest) manifest="$2"; shift 2 ;;
      *) usage ;;
    esac
  done
  [ -n "$manifest" ] || manifest="$STATE/$id.manifest.json"
  [ -f "$manifest" ] || fail "manifest $manifest not found"
  local pg backend t_start t_ready
  pg="$(pg_image)"
  backend="$(backend_image)"
  cmd_discard >/dev/null

  say "stopping the stack (one PostgreSQL at a time on this machine)"
  compose stop caddy web1 web2 api1 api2 worker postgres >/dev/null
  t_start="$(now)"
  docker network create --internal "$restore_net" >/dev/null
  docker volume create "$restore_volume" >/dev/null
  say "copying base backup $id into a new volume"
  docker run --rm -u 0 -v "${PROJECT}_pgbackups:/backups:ro" -v "$restore_volume:/restore" \
    --entrypoint sh "$pg" -c "cp -a /backups/$id/. /restore/ && chown -R postgres:postgres /restore && chmod 700 /restore" ||
    fail "copy"
  local recovery="restore_command = 'cp /archive/%f %p'"
  if [ -n "$target" ]; then
    recovery="$recovery
recovery_target_time = '$target'
recovery_target_action = 'promote'"
  fi
  docker run --rm -u 0 -v "$restore_volume:/restore" --entrypoint sh "$pg" -c \
    "printf '%s\n' \"$recovery\" >> /restore/postgresql.auto.conf && touch /restore/recovery.signal && chown postgres:postgres /restore/postgresql.auto.conf /restore/recovery.signal" ||
    fail "recovery configuration"
  say "starting the restored copy on an isolated network (replaying ${target:-to the latest WAL})"
  docker run -d --name lattice-restore-db --network "$restore_net" --network-alias postgres \
    -v "$restore_volume:/var/lib/postgresql/data" -v "${PROJECT}_pgarchive:/archive:ro" \
    -e POSTGRES_PASSWORD=unused "$pg" postgres -c archive_mode=off >/dev/null
  local i
  for i in $(seq 1 180); do
    if docker exec -u postgres lattice-restore-db psql -X -At -d lattice \
      -c "SELECT NOT pg_is_in_recovery()" 2>/dev/null | grep -q t; then
      break
    fi
    sleep 1
  done
  docker exec -u postgres lattice-restore-db psql -X -At -d lattice -c "SELECT NOT pg_is_in_recovery()" | grep -q t ||
    fail "the restored copy did not finish recovery"
  t_ready="$(now)"
  local restore_seconds
  restore_seconds="$(python3 -c "import sys; print(f'{float(sys.argv[2]) - float(sys.argv[1]):.1f}')" "$t_start" "$t_ready")"
  say "recovered in ${restore_seconds} s; verifying"

  local owner app worker key
  owner="$(env_value LATTICE_OWNER_PASSWORD)"
  app="$(env_value LATTICE_APP_PASSWORD)"
  worker="$(env_value LATTICE_WORKER_PASSWORD)"
  key="$(env_value LATTICE_CLIENT_KEY_SECRET)"
  local run=(docker run --rm --network "$restore_net" --read-only --tmpfs /tmp --cap-drop ALL
    --security-opt no-new-privileges:true)
  "${run[@]}" -e LATTICE_ENV=production \
    -e LATTICE_MIGRATION_DATABASE_URL="postgresql+asyncpg://lattice_owner:${owner}@postgres:5432/lattice" \
    -v "$ROOT/$manifest:/srv/manifest.json:ro" --entrypoint lattice "$backend" \
    verify-restore --manifest /srv/manifest.json || fail "verify-restore"
  local api_env=(-e LATTICE_ENV=production
    -e LATTICE_DATABASE_URL="postgresql+asyncpg://lattice_app:${app}@postgres:5432/lattice"
    -e LATTICE_PUBLIC_URL=https://localhost:8443 -e LATTICE_SESSION_COOKIE_SECURE=true
    -e LATTICE_DEV_AUTH_ENABLED=false -e LATTICE_QUERY_EMBEDDING=none
    -e LATTICE_CLIENT_KEY_SECRET="$key" -e LATTICE_METRICS_ADDRESS=127.0.0.1:9464)
  "${run[@]}" "${api_env[@]}" --entrypoint lattice "$backend" check-db-role || fail "check-db-role"
  say "PASS  check-db-role (lattice_app) against the restored copy"
  "${run[@]}" -e LATTICE_ENV=production \
    -e LATTICE_WORKER_DATABASE_URL="postgresql+asyncpg://lattice_worker:${worker}@postgres:5432/lattice" \
    -e LATTICE_METRICS_ADDRESS=127.0.0.1:9465 \
    --entrypoint lattice "$backend" worker --once || fail "worker --once"
  say "PASS  worker --once against the restored copy"
  docker run -d --name lattice-restore-api --network "$restore_net" --read-only --tmpfs /tmp \
    --cap-drop ALL --security-opt no-new-privileges:true "${api_env[@]}" "$backend" >/dev/null
  local ready=""
  for i in $(seq 1 60); do
    if docker exec lattice-restore-api python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/v1/health/ready', timeout=3)" 2>/dev/null; then
      ready=yes
      break
    fi
    sleep 1
  done
  [ -n "$ready" ] || fail "an api instance did not reach readiness against the restored copy"
  say "PASS  api readiness against the restored copy"
  docker rm -f lattice-restore-api >/dev/null

  say "scanning the backup, the archive and the restored copy for every configured secret value"
  local patterns
  patterns="$(mktemp)"
  sed -n 's/^[A-Z_]*=//p' "$REFERENCE_ENV" | awk 'length($0) >= 12' >"$patterns"
  local hits
  hits="$(docker run --rm -u 0 -v "$patterns:/patterns:ro" -v "${PROJECT}_pgbackups:/backups:ro" \
    -v "${PROJECT}_pgarchive:/archive:ro" -v "$restore_volume:/restore:ro" --entrypoint sh "$pg" \
    -c "grep -r -a -F -l -f /patterns /backups/$id /archive /restore 2>/dev/null | wc -l")"
  local count
  count="$(wc -l <"$patterns" | tr -d ' ')"
  rm -f "$patterns"
  [ "$hits" = "0" ] || fail "$hits file(s) contain a configured secret value"
  say "PASS  secret scan: none of $count configured secret values found"
  printf 'restore=%s\ntarget=%s\nrecovery_seconds=%s\n' "$id" "${target:-latest}" "$restore_seconds" \
    >>"$STATE/$id.env"
  say "restore of $id (${target:-latest}) verified; recovery ${restore_seconds} s"
  say "the stack is stopped: deploy/backup.sh discard-restore && make prod-up to resume"
}

[ $# -ge 1 ] || usage
command="$1"
shift
case "$command" in
  backup) cmd_backup ;;
  manifest) cmd_manifest "$@" ;;
  list) cmd_list ;;
  restore) cmd_restore "$@" ;;
  discard-restore) cmd_discard ;;
  *) usage ;;
esac
