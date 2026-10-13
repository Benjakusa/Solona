"""Add an ordered cursor for resumable device change feeds.

Revision ID: 0003_sync_event_sequence
Revises: 0002_user_membership_discovery
"""
from alembic import op
import sqlalchemy as sa

revision = "0003_sync_event_sequence"
down_revision = "0002_user_membership_discovery"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("sync_events", sa.Column("sequence", sa.BigInteger(), sa.Identity(), nullable=False))
    op.create_index("ix_sync_events_sequence", "sync_events", ["sequence"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_sync_events_sequence", table_name="sync_events")
    op.drop_column("sync_events", "sequence")
