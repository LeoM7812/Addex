"""addon p2p observed

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-08
"""

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "addons",
        sa.Column("p2p_observed", sa.Boolean, server_default=sa.false(), nullable=False),
    )


def downgrade() -> None:
    op.drop_column("addons", "p2p_observed")
