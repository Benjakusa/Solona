"""Allow a logged-in user to discover only their own tenant memberships.

Revision ID: 0002_user_membership_discovery
Revises: 0001_initial
"""
from alembic import op

revision = "0002_user_membership_discovery"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE POLICY own_membership_discovery ON tenant_memberships FOR SELECT "
        "USING (current_setting('app.user_id', true) IS NOT NULL "
        "AND user_id::text = current_setting('app.user_id', true))"
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS own_membership_discovery ON tenant_memberships")
