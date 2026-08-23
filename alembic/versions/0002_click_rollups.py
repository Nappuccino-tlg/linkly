"""click rollups: click_daily, referrer_daily

Revision ID: 0002
Revises: 0001
Create Date: 2026-08-23
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "click_daily",
        sa.Column("link_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("clicks", sa.BigInteger(), nullable=False),
        sa.Column("unique_visitors", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["link_id"], ["links.id"], ondelete="CASCADE"),
        # (link_id, day) is both the identity of a row and the only way it is ever read,
        # so the primary key index is the whole access plan for /stats.
        sa.PrimaryKeyConstraint("link_id", "day"),
    )

    op.create_table(
        "referrer_daily",
        sa.Column("link_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        # Bounded because it is part of a btree key -- see app/models.py.
        sa.Column("referrer", sa.String(length=512), nullable=False),
        sa.Column("clicks", sa.BigInteger(), nullable=False),
        sa.ForeignKeyConstraint(["link_id"], ["links.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("link_id", "day", "referrer"),
    )

    # Retention deletes by age across every link; the existing (link_id, clicked_at)
    # index leads with the wrong column to serve that.
    op.create_index("ix_clicks_clicked_at", "clicks", ["clicked_at"])


def downgrade() -> None:
    op.drop_index("ix_clicks_clicked_at", table_name="clicks")
    op.drop_table("referrer_daily")
    op.drop_table("click_daily")
