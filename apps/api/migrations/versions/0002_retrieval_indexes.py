"""Add production retrieval indexes.

Revision ID: 0002
Revises: 0001
"""
from typing import Sequence

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    bind.exec_driver_sql(
        """
        CREATE INDEX IF NOT EXISTS ix_chunks_embedding_hnsw
        ON chunks USING hnsw (embedding vector_cosine_ops)
        WITH (m = 16, ef_construction = 96)
        """
    )
    bind.exec_driver_sql(
        """
        CREATE INDEX IF NOT EXISTS ix_chunks_content_fts
        ON chunks USING gin (
            to_tsvector('simple', coalesce(section_path, '') || ' ' || content)
        )
        """
    )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    bind.exec_driver_sql("DROP INDEX IF EXISTS ix_chunks_content_fts")
    bind.exec_driver_sql("DROP INDEX IF EXISTS ix_chunks_embedding_hnsw")
