"""Every model module, imported once: the complete metadata Alembic and the schema drift
check compare against."""

from sqlalchemy import MetaData


def metadata() -> MetaData:
    from lattice.authz import models as authz_models  # noqa: F401
    from lattice.db.base import Base
    from lattice.identity import models as identity_models  # noqa: F401
    from lattice.ingestion import models as ingestion_models  # noqa: F401
    from lattice.jobs import models as job_models  # noqa: F401
    from lattice.orgs import models as org_models  # noqa: F401
    from lattice.providers import models as provider_models  # noqa: F401
    from lattice.ratelimit import models as ratelimit_models  # noqa: F401
    from lattice.sources import models as source_models  # noqa: F401
    from lattice.tracing import models as tracing_models  # noqa: F401

    return Base.metadata
