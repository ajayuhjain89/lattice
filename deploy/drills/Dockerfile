# syntax=docker/dockerfile:1.7
# Deployment drills (Phase 11, P11.2): deliberately different backend releases built on
# top of a real backend image, for the local reference stack ONLY. Never published; CI
# never builds them. Each changes exactly one thing:
#
#   relabel              the same code under another revision (a release without a
#                        schema change: rolling deploy, rollback)
#   schema               adds revision 0014 (no DDL, two seconds, declared reversible)
#                        and expects it: a migrating release (stop-the-world)
#   schema-irreversible  the same revision declared irreversible: rollback is refused
#   failing-migration    revision 0014 fails inside its transaction
#   wrong-port           the API listens on 8001: configuration valid, readiness never
#   public-docs          production serves /docs: ready, but the smoke check fails
#   bad-config           an invalid setting baked in: check-config refuses the release
#
#   docker build -f deploy/drills/Dockerfile --build-arg BASE=<backend image> \
#     --build-arg DRILL=<kind> --build-arg LATTICE_BUILD_REVISION=<label> \
#     [--target wrong-port|bad-config] -t lattice-backend:drill-<kind> deploy/drills
ARG BASE=lattice-backend:local
FROM ${BASE} AS drill
ARG DRILL=relabel
ARG LATTICE_BUILD_REVISION=drill
LABEL org.opencontainers.image.revision="${LATTICE_BUILD_REVISION}" \
      org.opencontainers.image.description="Lattice deployment drill (${DRILL}); never released"
ENV LATTICE_BUILD_REVISION=${LATTICE_BUILD_REVISION} \
    LATTICE_DRILL_API_PORT=8000
USER root
COPY 0014_drill.py /tmp/0014_drill.py
RUN set -eu; \
    site="$(python -c 'import lattice, os; print(os.path.dirname(lattice.__file__))')"; \
    case "${DRILL}" in \
      relabel|wrong-port|bad-config) ;; \
      schema|schema-irreversible|failing-migration) \
        reversible="yes (the downgrade changes nothing)"; \
        [ "${DRILL}" = schema ] || reversible="no (declared irreversible for the drill)"; \
        sed "s/@REVERSIBLE@/${reversible}/" /tmp/0014_drill.py > alembic/versions/0014_drill.py; \
        [ "${DRILL}" != failing-migration ] || sed -i 's/^FAIL = False/FAIL = True/' alembic/versions/0014_drill.py; \
        sed -i 's/^SCHEMA_REVISION: Final = "0013"$/SCHEMA_REVISION: Final = "0014"/' "${site}/db/schema.py"; \
        grep -q 'SCHEMA_REVISION: Final = "0014"' "${site}/db/schema.py" ;; \
      public-docs) \
        sed -i -e 's/docs_url=None if production else "\/docs"/docs_url="\/docs"/' \
          -e 's/openapi_url=None if production else "\/openapi.json"/openapi_url="\/openapi.json"/' \
          "${site}/api/app.py"; \
        grep -q 'openapi_url="/openapi.json"' "${site}/api/app.py" ;; \
      *) echo "unknown drill ${DRILL}"; exit 1 ;; \
    esac; \
    find "${site}" -name '*.pyc' -delete; python -m compileall -q "${site}" alembic; \
    rm /tmp/0014_drill.py
USER 10001:10001
CMD ["sh", "-c", "exec uvicorn lattice.api.app:create_app --factory --host 0.0.0.0 --port \"${LATTICE_DRILL_API_PORT}\" --no-access-log --no-proxy-headers --timeout-graceful-shutdown 150"]

FROM drill AS wrong-port
ENV LATTICE_DRILL_API_PORT=8001

FROM drill AS bad-config
ENV LATTICE_RATE_LIMIT_ASK_PER_MINUTE=0

# Default target: the plain drill.
FROM drill
