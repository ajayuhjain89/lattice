# syntax=docker/dockerfile:1.7
# The Lattice web image (Phase 11, ADR-016): the Next.js standalone server, no secrets.
# Build from apps/web:  docker build -f Dockerfile .   (context: .dockerignore)
# /api/* never reaches this server in deployment: the ingress routes it to the API.

ARG NODE_IMAGE=node:22-bookworm-slim@sha256:43ac6c60b8f89723f746e8a92ce91abd5017e627ce1ddfe4238355d3a30b772c

# --- build: locked install (pnpm via corepack, pinned by package.json) and next build ---
FROM ${NODE_IMAGE} AS build
ENV PNPM_HOME=/pnpm \
    COREPACK_ENABLE_DOWNLOAD_PROMPT=0 \
    NEXT_TELEMETRY_DISABLED=1
RUN corepack enable
WORKDIR /app
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
RUN --mount=type=cache,id=pnpm-store,target=/pnpm/store \
    pnpm config set store-dir /pnpm/store && pnpm install --frozen-lockfile
COPY . .
RUN LATTICE_WEB_STANDALONE=1 pnpm build

# --- runtime: the standalone server, its static assets and public files only -----------
FROM ${NODE_IMAGE} AS runtime
ARG LATTICE_BUILD_REVISION=unknown
ARG LATTICE_BUILD_VERSION=unknown
LABEL org.opencontainers.image.title="lattice-web" \
      org.opencontainers.image.description="Lattice web app (Next.js standalone server)" \
      org.opencontainers.image.source="https://github.com/ajayuhjain89/lattice" \
      org.opencontainers.image.revision="${LATTICE_BUILD_REVISION}" \
      org.opencontainers.image.version="${LATTICE_BUILD_VERSION}"
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    HOSTNAME=0.0.0.0 \
    PORT=3000 \
    LATTICE_BUILD_REVISION=${LATTICE_BUILD_REVISION} \
    LATTICE_BUILD_VERSION=${LATTICE_BUILD_VERSION}
WORKDIR /app
COPY --from=build --chown=root:root /app/.next/standalone ./
COPY --from=build --chown=root:root /app/.next/static ./.next/static
COPY --from=build --chown=root:root /app/public ./public
# The node image's unprivileged user; the files above stay root-owned (read-only to it).
USER node
EXPOSE 3000
CMD ["node", "server.js"]
