"""title aliases

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-07
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "titles",
        sa.Column("aliases", postgresql.ARRAY(sa.Text), server_default="{}", nullable=False),
    )


def downgrade() -> None:
    op.drop_column("titles", "aliases")
