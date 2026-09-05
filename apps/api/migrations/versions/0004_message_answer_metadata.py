"""persist answer metadata for conversation history

Revision ID: 0004
Revises: 0003
"""

from alembic import op
import sqlalchemy as sa


revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("messages", sa.Column("retrieval_ms", sa.Integer(), nullable=True))
    op.add_column("messages", sa.Column("generation_mode", sa.String(length=40), nullable=True))
    op.add_column("messages", sa.Column("confidence", sa.String(length=20), nullable=True))
    op.add_column("messages", sa.Column("evidence_coverage", sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column("messages", "evidence_coverage")
    op.drop_column("messages", "confidence")
    op.drop_column("messages", "generation_mode")
    op.drop_column("messages", "retrieval_ms")
