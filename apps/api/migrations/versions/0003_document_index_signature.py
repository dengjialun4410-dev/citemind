"""Track the model and parser used to build each document index.

Revision ID: 0003
Revises: 0002
"""
from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("documents", sa.Column("index_signature", sa.String(length=500), nullable=True))


def downgrade() -> None:
    op.drop_column("documents", "index_signature")
