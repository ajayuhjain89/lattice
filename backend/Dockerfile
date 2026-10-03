# syntax=docker/dockerfile:1.7
# The Lattice backend image (Phase 11, ADR-016): one image for every backend process.
#   api (default)   uvicorn, lattice_app            docs/deployment.md §2
#   worker          lattice worker, lattice_worker
#   migrate         alembic upgrade head, lattice_owner (one-off job)
#   operator        lattice <command>, lattice_owner (one-off)
# Build from the repository root:  docker build -f backend/Dockerfile .
# (backend/Dockerfile.dockerignore limits the context to backend/ and the bootstrap SQL.)
# No secret, .env, synthetic data, test fixture or model weight is ever copied in.

ARG PYTHON_IMAGE=python:3.12-slim-bookworm@sha256:54c85f3c47607a77f32adec749d3c81d1348bf25833671f512b26a9b6d778cb3
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.12.19@sha256:04d046b13e60d6bcec73cbc5e1cad25d680dea90c8573340950a0ac2d1aef424

FROM ${UV_IMAGE} AS uv

# --- build: the locked runtime dependencies and the lattice package into one venv ------
FROM ${PYTHON_IMAGE} AS build
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PYTHON=/usr/local/bin/python3.12 \
    UV_PROJECT_ENVIRONMENT=/opt/lattice/venv
WORKDIR /src
COPY backend/pyproject.toml backend/uv.lock ./
# Dependencies first (cached while only sources change); --extra local is the ONNX
# runtime for LATTICE_QUERY/SYNC_EMBEDDING=local (ADR-009), never the weights.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --extra local --no-install-project
COPY backend/src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --extra local --no-editable

# --- runtime: the venv, migrations and bootstrap SQL; no build tools, no uv ------------
FROM ${PYTHON_IMAGE} AS runtime
ARG LATTICE_BUILD_REVISION=unknown
ARG LATTICE_BUILD_VERSION=unknown
LABEL org.opencontainers.image.title="lattice-backend" \
      org.opencontainers.image.description="Lattice API, worker, migrations and operator commands" \
      org.opencontainers.image.source="https://github.com/ajayuhjain89/lattice" \
      org.opencontainers.image.revision="${LATTICE_BUILD_REVISION}" \
      org.opencontainers.image.version="${LATTICE_BUILD_VERSION}"
RUN groupadd --system --gid 10001 lattice \
 && useradd --system --uid 10001 --gid lattice --no-create-home \
      --home-dir /nonexistent --shell /usr/sbin/nologin lattice
COPY --from=build /opt/lattice/venv /opt/lattice/venv
WORKDIR /opt/lattice/app
COPY backend/alembic.ini ./alembic.ini
COPY backend/alembic ./alembic
COPY infra/postgres/bootstrap.sql infra/postgres/bootstrap_database.sql ./infra/postgres/
ENV PATH=/opt/lattice/venv/bin:${PATH} \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    LATTICE_BUILD_REVISION=${LATTICE_BUILD_REVISION} \
    LATTICE_BUILD_VERSION=${LATTICE_BUILD_VERSION}
USER 10001:10001
EXPOSE 8000
# The API. --no-proxy-headers: forwarding headers are trusted only from
# LATTICE_TRUSTED_PROXIES, inside the app (uvicorn's own default trusts 127.0.0.1).
# --timeout-graceful-shutdown 150: an Ask may run the 120 s generation deadline; give the
# container a stop grace of at least 160 s (docs/deployment.md §10).
CMD ["uvicorn", "lattice.api.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--no-access-log", "--no-proxy-headers", "--timeout-graceful-shutdown", "150"]
