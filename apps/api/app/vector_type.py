from sqlalchemy import JSON
from sqlalchemy.types import TypeDecorator

from pgvector.sqlalchemy import Vector


class EmbeddingVector(TypeDecorator):
    """Use native pgvector on PostgreSQL and JSON in zero-config SQLite mode."""

    impl = JSON
    cache_ok = True

    def __init__(self, dimensions: int = 384) -> None:
        super().__init__()
        self.dimensions = dimensions

    def load_dialect_impl(self, dialect):  # type: ignore[no-untyped-def]
        if dialect.name == "postgresql":
            return dialect.type_descriptor(Vector(self.dimensions))
        return dialect.type_descriptor(JSON())
