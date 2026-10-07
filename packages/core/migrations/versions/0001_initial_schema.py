"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-10-07
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

addon_status = postgresql.ENUM(
    "active", "no_streams", "needs_config", "broken", "disabled",
    name="addon_status", create_type=False,
)
id_scheme = postgresql.ENUM(
    "imdb", "kitsu", "mal", "anilist", "tmdb", name="id_scheme", create_type=False
)
check_status = postgresql.ENUM(
    "ok", "empty", "timeout", "http_error", "invalid", "error",
    name="check_status", create_type=False,
)

TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    for enum in (addon_status, id_scheme, check_status):
        enum.create(op.get_bind())

    op.create_table(
        "addons",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("manifest_url", sa.Text, nullable=False),
        sa.Column("base_url", sa.Text, nullable=False),
        sa.Column("manifest_id", sa.Text, nullable=False),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("version", sa.Text, nullable=False),
        sa.Column("description", sa.Text),
        sa.Column("logo", sa.Text),
        sa.Column("stream_scopes", postgresql.JSONB, nullable=False),
        sa.Column("p2p", sa.Boolean, nullable=False),
        sa.Column("adult", sa.Boolean, nullable=False),
        sa.Column("manifest", postgresql.JSONB, nullable=False),
        sa.Column("status", addon_status, nullable=False),
        sa.Column("last_fetched_at", TS),
        sa.Column("last_error", sa.Text),
        sa.Column("created_at", TS, server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", TS, server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_addons"),
        sa.UniqueConstraint("manifest_url", name="uq_addons_manifest_url"),
    )
    op.create_index("ix_addons_manifest_id", "addons", ["manifest_id"])

    op.create_table(
        "titles",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("type", sa.Text, nullable=False),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("year", sa.SmallInteger),
        sa.Column("is_anime", sa.Boolean, nullable=False),
        sa.Column("poster", sa.Text),
        sa.Column("popularity_rank", sa.Integer),
        sa.Column("created_at", TS, server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", TS, server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_titles"),
    )
    op.create_index("ix_titles_popularity_rank", "titles", ["popularity_rank"])

    op.create_table(
        "title_ids",
        sa.Column("title_id", sa.Integer, nullable=False),
        sa.Column("scheme", id_scheme, nullable=False),
        sa.Column("value", sa.Text, nullable=False),
        sa.PrimaryKeyConstraint("title_id", "scheme", name="pk_title_ids"),
        sa.ForeignKeyConstraint(
            ["title_id"], ["titles.id"], ondelete="CASCADE",
            name="fk_title_ids_title_id_titles",
        ),
        sa.UniqueConstraint("scheme", "value", name="uq_title_ids_scheme_value"),
    )

    op.create_table(
        "availability",
        sa.Column("addon_id", sa.Integer, nullable=False),
        sa.Column("title_id", sa.Integer, nullable=False),
        sa.Column("probe_id", sa.Text, nullable=False),
        sa.Column("status", check_status, nullable=False),
        sa.Column("has_streams", sa.Boolean),
        sa.Column("stream_count", sa.Integer),
        sa.Column("latency_ms", sa.Integer),
        sa.Column("http_status", sa.SmallInteger),
        sa.Column("last_checked", TS, nullable=False),
        sa.Column("last_answered", TS),
        sa.Column("next_check_at", TS, nullable=False),
        sa.Column("consecutive_failures", sa.Integer, nullable=False),
        sa.PrimaryKeyConstraint("addon_id", "title_id", name="pk_availability"),
        sa.ForeignKeyConstraint(
            ["addon_id"], ["addons.id"], ondelete="CASCADE",
            name="fk_availability_addon_id_addons",
        ),
        sa.ForeignKeyConstraint(
            ["title_id"], ["titles.id"], ondelete="CASCADE",
            name="fk_availability_title_id_titles",
        ),
    )
    op.create_index("ix_availability_title_id", "availability", ["title_id"])
    op.create_index("ix_availability_next_check_at", "availability", ["next_check_at"])


def downgrade() -> None:
    op.drop_table("availability")
    op.drop_table("title_ids")
    op.drop_table("titles")
    op.drop_table("addons")
    for enum in (check_status, id_scheme, addon_status):
        enum.drop(op.get_bind())
