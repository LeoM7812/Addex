"""title parent

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-07
"""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("titles", sa.Column("parent_id", sa.Integer))
    op.create_foreign_key(
        "fk_titles_parent_id_titles", "titles", "titles", ["parent_id"], ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_titles_parent_id", "titles", ["parent_id"])


def downgrade() -> None:
    op.drop_index("ix_titles_parent_id", "titles")
    op.drop_constraint("fk_titles_parent_id_titles", "titles", type_="foreignkey")
    op.drop_column("titles", "parent_id")
