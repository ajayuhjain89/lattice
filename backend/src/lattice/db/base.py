"""Declarative base shared by all ORM models.

The naming convention matches the constraint names written in the Alembic migrations,
so `alembic check` can detect drift between models and the migrated schema.
"""

import uuid
from datetime import datetime

from sqlalchemy import MetaData
from sqlalchemy.dialects.postgresql import TIMESTAMP, UUID
from sqlalchemy.orm import DeclarativeBase

NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
    type_annotation_map = {  # noqa: RUF012 - SQLAlchemy reads this class attribute
        datetime: TIMESTAMP(timezone=True),
        uuid.UUID: UUID(as_uuid=True),
    }
