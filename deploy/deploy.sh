#!/usr/bin/env bash
# Lattice deployment for the local reference stack (Phase 11, P11.2; docs/deployment.md §9).
#
# Deploys immutable images -- every reference is resolved to its content-addressed image
# ID before anything changes, and that ID is what runs and what is recorded -- in the
# order D-23 requires:
#
#   release without a schema change   rolling: api1, api2 (each readiness-gated), worker,
#                                     web1, web2; no downtime (the ingress fails over)
#   release with a migration          stop-the-world: /api/* maintenance (503), stop api ×2
#                                     and worker, `lattice migrate` (lock, fence,
#                                     pre-flight), start the new build, readiness, routing
#                                     back, web ×2; the unavailable interval is measured
#   rollback                          the previous release: rolling while its schema equals
#                                     the database's; across a migration only with
#                                     --allow-downgrade AND every undone revision declared
#                                     reversible (stop-the-world, run by the current build);
#                                     otherwise refused: roll forward
#
# Usage (from anywhere; works on the repository root):
#   deploy/deploy.sh status
#   deploy/deploy.sh release --backend REF --web REF [--expect-revision SHA] [--allow-migration]
#   deploy/deploy.sh rollback [--allow-downgrade]
#
# LATTICE_DEPLOY_VERIFY=1 adds deploy/compose.verify.yaml (LATTICE_ENV=test overlay);
# otherwise the stack runs its production defaults and the smoke checks the production
# surface. Exit codes: 0 deployed; 1 refused or failed (the message says what is running);
# 2 usage. Prints identifiers, revisions and fixed reasons only, never secrets.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

STATE=deploy/state
LOG="$STATE/deploy.log"
IMAGES="$STATE/images.env"
PREVIOUS="$STATE/previous.env"
LOCK="$STATE/lock"
MAINTENANCE=deploy/maintenance/api
REFERENCE_ENV=deploy/.env.reference
CA=deploy/.reference-ca.crt
READY_TIMEOUT="${LATTICE_DEPLOY_READY_TIMEOUT:-180}"
# After an instance is ready, wait for the ingress's next health check (Caddy: every 5 s)
# to route to it again before the next instance stops.
SETTLE="${LATTICE_DEPLOY_SETTLE_SECONDS:-7}"

say() { printf '%s %s\n' "$(date -u +%H:%M:%S)" "$*"; }
fail() { say "FAILED: $*"; record "failed" "$*"; exit 1; }
usage() { sed -n '/^# Usage/,/^# LATTICE_DEPLOY_VERIFY/p' "$0" >&2; exit 2; }

compose() {
  local files=(-f compose.production.yaml)
  [ "${LATTICE_DEPLOY_VERIFY:-}" = "1" ] && files+=(-f deploy/compose.verify.yaml)
  local envs=(--env-file "$REFERENCE_ENV")
  [ -f "$IMAGES" ] && envs+=(--env-file "$IMAGES")
  docker compose "${envs[@]}" "${files[@]}" "$@"
}

# compose with an explicit backend/web image pair (shell environment beats --env-file).
with_images() {
  local backend="$1" web="$2"
  shift 2
  LATTICE_BACKEND_IMAGE="$backend" LATTICE_WEB_IMAGE="$web" compose "$@"
}

record() {
  mkdir -p "$STATE"
  printf '%s\t%s\t%s\tbackend=%s\tweb=%s\t%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
    "${ACTION:-?}" "$1" "${NEW_BACKEND:-?}" "${NEW_WEB:-?}" "${2:-}" >>"$LOG"
}

image_id() {
  docker image inspect --format '{{.Id}}' "$1" 2>/dev/null || {
    say "FAILED: image $1 is not available locally (pull it by digest first)"
    exit 1
  }
}

label_revision() {
  docker image inspect --format '{{index .Config.Labels "org.opencontainers.image.revision"}}' "$1"
}

# The backend build's own report: revision baked into its environment + schema revision.
build_field() {  # image field
  docker run --rm --network none --read-only "$1" lattice version --json |
    python3 -c 'import json, sys; print(json.load(sys.stdin)[sys.argv[1]])' "$2"
}

running_image() {  # service -> image ID of its container (empty if none)
  local id
  id="$(compose ps -a -q "$1" 2>/dev/null | head -1)"
  [ -n "$id" ] && docker inspect --format '{{.Image}}' "$id" || true
}

db_state() {  # backend web -> "state database_revision" (as the new build sees it)
  with_images "$1" "$2" run --rm --no-deps -T ops migrate --status --json |
    python3 -c 'import json, sys; d = json.loads(sys.stdin.read().strip().splitlines()[-1]); print(d["state"], d["database_revision"])'
}

acquire_lock() {
  mkdir -p "$STATE"
  mkdir "$LOCK" 2>/dev/null || {
    say "FAILED: another deployment is running ($LOCK exists; remove it only if none is)"
    exit 1
  }
  trap 'rmdir "$LOCK" 2>/dev/null || true' EXIT
}

smoke() {  # revision
  compose cp caddy:/data/caddy/pki/authorities/local/root.crt "$CA" >/dev/null
  local production=(--production)
  [ "${LATTICE_DEPLOY_VERIFY:-}" = "1" ] && production=()
  compose run --rm --no-deps -T -v "$ROOT/$CA:/srv/reference-ca.crt:ro" ops \
    smoke --url https://caddy:8443 --ca-file /srv/reference-ca.crt \
    --instance http://api1:8000 --instance http://api2:8000 \
    --worker-metrics http://worker:9465/metrics \
    --instance-metrics http://api1:9464/metrics --instance-metrics http://api2:9464/metrics \
    --expect-revision "$1" ${production[@]+"${production[@]}"}
}

check_config() {  # backend web
  local service process
  for pair in api1:api worker:worker migrate:migrate; do
    service="${pair%%:*}"
    process="${pair##*:}"
    with_images "$1" "$2" run --rm --no-deps -T "$service" lattice check-config "$process" ||
      fail "configuration of $process is invalid (nothing was changed)"
  done
}

roll() {  # backend web service... : replace each service, readiness-gated, in order
  local backend="$1" web="$2" service old
  shift 2
  for service in "$@"; do
    old="$(running_image "$service")"
    say "rolling $service"
    if ! with_images "$backend" "$web" up -d --no-deps --wait --wait-timeout "$READY_TIMEOUT" \
      "$service"; then
      if [ -n "$old" ]; then
        say "$service did not become ready; restoring its previous image"
        if [ "${service#web}" != "$service" ]; then
          with_images "$OLD_BACKEND" "$old" up -d --no-deps --wait "$service" || true
        else
          with_images "$old" "$OLD_WEB" up -d --no-deps --wait "$service" || true
        fi
      fi
      fail "$service did not become ready with the new image; previous image restored"
    fi
    case "$service" in api* | web*) sleep "$SETTLE" ;; esac
  done
}

# Stop-the-world (D-23): maintenance, stop api ×2 + worker, run the schema change with
# $1 (backend image) and $2.. (the `lattice migrate` arguments), start the new build.
stop_the_world() {
  local runner="$1" started ended
  shift
  started="$(date +%s)"
  : >"$MAINTENANCE"
  say "maintenance on: /api/* answers 503"
  compose stop api1 api2 worker
  if ! with_images "$runner" "$OLD_WEB" run --rm --no-deps -T ops migrate "$@"; then
    say "schema change failed or was refused; the schema is unchanged: restarting the previous build"
    with_images "$OLD_BACKEND" "$OLD_WEB" up -d --no-deps --wait --wait-timeout "$READY_TIMEOUT" \
      api1 api2 worker || true
    rm -f "$MAINTENANCE"
    fail "schema change did not complete; previous build ${OLD_BACKEND:7:12} restarted"
  fi
  if ! with_images "$NEW_BACKEND" "$NEW_WEB" up -d --no-deps --wait \
    --wait-timeout "$READY_TIMEOUT" api1 api2 worker; then
    fail "the new build did not become ready after the schema change; maintenance stays on: roll forward"
  fi
  sleep "$SETTLE"  # the ingress marks the new instances healthy before routing returns
  rm -f "$MAINTENANCE"
  ended="$(date +%s)"
  UNAVAILABLE="$((ended - started))"
  say "maintenance off: API unavailable for ${UNAVAILABLE} s"
  sleep "$SETTLE"
  roll "$NEW_BACKEND" "$NEW_WEB" web1 web2
}

write_state() {
  [ -f "$IMAGES" ] && cp "$IMAGES" "$PREVIOUS"
  [ -f "$IMAGES" ] || printf 'LATTICE_BACKEND_IMAGE=%s\nLATTICE_WEB_IMAGE=%s\n' \
    "$OLD_BACKEND" "$OLD_WEB" >"$PREVIOUS"
  printf 'LATTICE_BACKEND_IMAGE=%s\nLATTICE_WEB_IMAGE=%s\n' "$NEW_BACKEND" "$NEW_WEB" >"$IMAGES"
}

release() {
  local backend_ref="" web_ref="" expect="" allow_migration=0 allow_downgrade=0
  while [ $# -gt 0 ]; do
    case "$1" in
      --backend) backend_ref="$2"; shift 2 ;;
      --web) web_ref="$2"; shift 2 ;;
      --expect-revision) expect="$2"; shift 2 ;;
      --allow-migration) allow_migration=1; shift ;;
      --allow-downgrade) allow_downgrade=1; shift ;;
      *) usage ;;
    esac
  done
  [ -n "$backend_ref" ] && [ -n "$web_ref" ] || usage
  acquire_lock

  # 1. Pre-flight: immutable identity, build metadata, configuration, schema state.
  docker compose --env-file "$REFERENCE_ENV" -f compose.production.yaml config -q ||
    fail "the stack configuration is incomplete (missing variables); nothing was changed"
  NEW_BACKEND="$(image_id "$backend_ref")"
  NEW_WEB="$(image_id "$web_ref")"
  OLD_BACKEND="$(running_image api1)"
  OLD_WEB="$(running_image web1)"
  [ -n "$OLD_BACKEND" ] && [ -n "$OLD_WEB" ] ||
    fail "no running release found (start the stack with make prod-up first)"
  local revision baked schema web_revision
  revision="$(label_revision "$NEW_BACKEND")"
  baked="$(build_field "$NEW_BACKEND" revision)"
  schema="$(build_field "$NEW_BACKEND" schema_revision)"
  web_revision="$(label_revision "$NEW_WEB")"
  say "backend ${NEW_BACKEND:7:12} revision $revision schema $schema; web ${NEW_WEB:7:12} revision $web_revision"
  [ -n "$revision" ] && [ "$revision" = "$baked" ] ||
    fail "backend image label ($revision) and baked revision ($baked) differ"
  [ "$web_revision" = "$revision" ] ||
    fail "revision mismatch: backend $revision, web $web_revision (one release = one commit)"
  [ -z "$expect" ] || [ "$expect" = "$revision" ] ||
    fail "revision mismatch: expected $expect, images are $revision"
  check_config "$NEW_BACKEND" "$NEW_WEB"
  local state db
  read -r state db <<<"$(db_state "$NEW_BACKEND" "$NEW_WEB")" || true
  say "database at ${db:-?} ($state for this build)"

  case "$state" in
    current)
      # 2a. No schema change: rolling, no downtime.
      roll "$NEW_BACKEND" "$NEW_WEB" api1 api2 worker web1 web2
      ;;
    pending)
      [ "$allow_migration" = 1 ] ||
        fail "this release migrates $db -> $schema: stop-the-world (D-23); re-run with --allow-migration"
      # 2b. Schema change: stop-the-world, migrated by the new build.
      stop_the_world "$NEW_BACKEND"
      ;;
    ahead)
      [ "$allow_downgrade" = 1 ] ||
        fail "the database ($db) is newer than this build ($schema): roll forward (or rollback --allow-downgrade if every newer revision is declared reversible)"
      # 2c. Rollback across a reversible migration: the running build, which knows the
      # newer revisions, undoes them -- decided before anything stops.
      with_images "$OLD_BACKEND" "$OLD_WEB" run --rm --no-deps -T ops \
        migrate --downgrade-to "$schema" --check ||
        fail "downgrade to $schema refused (nothing was stopped): roll forward"
      stop_the_world "$OLD_BACKEND" --downgrade-to "$schema"
      ;;
    *)
      fail "the database is not migrated or unreadable (state '${state:-?}'): use make prod-up"
      ;;
  esac

  write_state
  # 3. Smoke: the public path, both instances, the worker, and every process's revision.
  if ! smoke "$revision"; then
    fail "smoke failed after deploying $revision (it is running): deploy/deploy.sh rollback"
  fi
  record "ok" "revision=$revision schema=$schema${UNAVAILABLE:+ unavailable=${UNAVAILABLE}s}"
  say "deployed $revision"
}

rollback() {
  local downgrade=()
  [ "${1:-}" = "--allow-downgrade" ] && downgrade=(--allow-downgrade)
  [ -f "$PREVIOUS" ] || { say "FAILED: no previous release recorded"; exit 1; }
  local backend web
  backend="$(sed -n 's/^LATTICE_BACKEND_IMAGE=//p' "$PREVIOUS")"
  web="$(sed -n 's/^LATTICE_WEB_IMAGE=//p' "$PREVIOUS")"
  ACTION=rollback
  say "rolling back to backend ${backend:7:12}, web ${web:7:12}"
  # A rollback never migrates forward; across a schema change only with --allow-downgrade.
  release --backend "$backend" --web "$web" ${downgrade[@]+"${downgrade[@]}"}
}

status() {
  [ -f "$IMAGES" ] && sed 's/^/recorded: /' "$IMAGES"
  local service id
  for service in api1 api2 worker web1 web2; do
    id="$(running_image "$service")"
    [ -n "$id" ] && say "$service: ${id:7:12} revision $(label_revision "$id")"
  done
  with_images "$(running_image api1)" "$(running_image web1)" run --rm --no-deps -T ops \
    migrate --status
}

ACTION="${1:-}"
[ $# -gt 0 ] && shift
case "$ACTION" in
  release) release "$@" ;;
  rollback) rollback "$@" ;;
  status) status ;;
  *) usage ;;
esac
