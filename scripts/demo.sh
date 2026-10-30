#!/usr/bin/env bash
# `make demo` (Phase 14, D-46): one command from a fresh clone to Lattice running locally.
#
# Checks the prerequisites, installs the locked dependencies, starts the development
# database, loads the synthetic Halden and Veridian organizations, and runs the API and
# the web app together until Ctrl-C. Every step is an existing `make` target and is safe
# to re-run: migrations, seed and sync change nothing the second time.
#
# No answer model is configured, so each question shows the evidence the person may see,
# with its sources, freshness and supersession (`evidence_only`). Nothing leaves the
# machine. Drafted answers need a real model: docs/demo.md, "Drafted answers".
set -euo pipefail
cd "$(dirname "$0")/.."

say() { printf '\n==> %s\n' "$*"; }
fail() { printf '\nmake demo: %s\n' "$*" >&2; exit 1; }

node_supported() {
  # The web app needs Node ^22.19 or >= 24 (README, O11).
  command -v node >/dev/null 2>&1 || return 1
  local major minor
  IFS=. read -r major minor _ <<<"$(node --version | tr -d v)"
  { [ "$major" -eq 22 ] && [ "$minor" -ge 19 ]; } || [ "$major" -ge 24 ]
}

say "Checking prerequisites"
command -v docker >/dev/null 2>&1 || fail "Docker is not installed (Docker Desktop with Compose v2)."
docker info >/dev/null 2>&1 || fail "Docker is not running: start Docker Desktop, then run make demo again."
command -v uv >/dev/null 2>&1 || fail "uv is not installed: https://docs.astral.sh/uv/"
if ! node_supported && [ -s "${NVM_DIR:-$HOME/.nvm}/nvm.sh" ]; then
  # An installed supported Node is picked for this run only; the shell's default is untouched.
  set +u
  # shellcheck disable=SC1091
  . "${NVM_DIR:-$HOME/.nvm}/nvm.sh"
  nvm use --silent 24 >/dev/null 2>&1 || nvm use --silent 22 >/dev/null 2>&1 || true
  set -u
fi
node_supported || fail "Node.js 22.19+ or 24+ is needed for the web app (found: $(node --version 2>/dev/null || echo none)). With nvm: nvm install 24"
command -v pnpm >/dev/null 2>&1 || fail "pnpm is not installed: corepack enable (ships with Node)"
echo "docker, uv, node $(node --version), pnpm $(pnpm --version): ok"

if [ ! -f .env ]; then
  say "Creating .env from .env.example (local-only placeholder credentials)"
  cp .env.example .env
fi

say "Installing backend dependencies"
make --no-print-directory setup
say "Starting PostgreSQL (127.0.0.1:5433)"
make --no-print-directory db-up
say "Applying migrations"
make --no-print-directory migrate
say "Loading the synthetic organizations, people and accounts"
make --no-print-directory seed
say "Ingesting their documents, tickets and chat (test embeddings: no model download)"
make --no-print-directory sync

say "Installing web dependencies"
make --no-print-directory web-setup

say "Starting the API and the web app (Ctrl-C stops both)"
pids=()
kill_tree() { # make starts uvicorn and Next.js as children: stop the whole tree
  local child
  for child in $(pgrep -P "$1" 2>/dev/null); do kill_tree "$child"; done
  kill "$1" 2>/dev/null || true
}
stop() {
  trap - INT TERM EXIT
  for pid in "${pids[@]}"; do kill_tree "$pid"; done
  wait 2>/dev/null || true
  echo
  echo "Stopped. The database keeps running with its data: make db-down stops it."
}
trap stop INT TERM EXIT
make --no-print-directory api & pids+=($!)
make --no-print-directory web & pids+=($!)

for _ in $(seq 1 60); do
  if curl -fs http://127.0.0.1:8000/v1/health/ready >/dev/null 2>&1 \
    && curl -fs -o /dev/null http://127.0.0.1:3000/ 2>/dev/null; then
    cat <<'EOF'

  Lattice is running:  http://127.0.0.1:3000

  Pick a person on the sign-in page (development sign-in: no password) and ask a
  question. docs/demo.md has six questions that show what Lattice does.

  Ctrl-C stops the API and the web app.
EOF
    wait
    exit 0
  fi
  sleep 2
done
fail "the API or the web app did not start within two minutes (see the output above)."
