"""Column types PostgreSQL provides and SQLAlchemy does not: pgvector's ``vector``.

A small ``UserDefinedType`` instead of the ``pgvector`` package, which would pull in
numpy for one column. Values cross the driver as pgvector's text form
(``[0.1,0.2,...]``), which asyncpg sends as an untyped string literal and PostgreSQL
casts to ``vector``. Reflection is registered so ``alembic check`` recognises the
column.
"""

from collections.abc import Callable, Sequence
from typing import Any

from sqlalchemy.dialects.postgresql.base import ischema_names
from sqlalchemy.types import UserDefinedType


class Vector(UserDefinedType[list[float]]):
    cache_ok = True

    def __init__(self, dimensions: int | None = None) -> None:
        self.dimensions = dimensions

    def get_col_spec(self, **kw: Any) -> str:
        return "VECTOR" if self.dimensions is None else f"VECTOR({self.dimensions})"

    def bind_processor(self, dialect: Any) -> Callable[[Sequence[float] | None], str | None]:
        def process(value: Sequence[float] | None) -> str | None:
            if value is None:
                return None
            if self.dimensions is not None and len(value) != self.dimensions:
                raise ValueError(f"vector has {len(value)} dimensions, expected {self.dimensions}")
            return "[" + ",".join(repr(float(v)) for v in value) + "]"

        return process

    def result_processor(
        self, dialect: Any, coltype: object
    ) -> Callable[[str | None], list[float] | None]:
        def process(value: str | None) -> list[float] | None:
            if value is None:
                return None
            return [float(v) for v in value.strip("[]").split(",")] if value != "[]" else []

        return process


ischema_names["vector"] = Vector
