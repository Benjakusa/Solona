"""Persist offline sale discounts and taxes on cloud sale records.

Revision ID: 0005_sale_replay_amounts
Revises: 0004_saas_billing_ops
"""
from alembic import op
import sqlalchemy as sa

revision = "0005_sale_replay_amounts"
down_revision = "0004_saas_billing_ops"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("daraja_attempts", sa.Column("daraja_environment", sa.String(16), nullable=True))
    op.add_column("daraja_attempts", sa.Column("business_shortcode", sa.String(32), nullable=True))
    op.add_column("sales", sa.Column("discount_amount", sa.Numeric(12, 2), nullable=False, server_default="0"))
    op.add_column("sales", sa.Column("tax_amount", sa.Numeric(12, 2), nullable=False, server_default="0"))
    op.create_check_constraint("sale_discount_nonnegative", "sales", "discount_amount >= 0")
    op.create_check_constraint("sale_tax_nonnegative", "sales", "tax_amount >= 0")


def downgrade() -> None:
    op.drop_constraint("sale_tax_nonnegative", "sales", type_="check")
    op.drop_constraint("sale_discount_nonnegative", "sales", type_="check")
    op.drop_column("sales", "tax_amount")
    op.drop_column("sales", "discount_amount")
    op.drop_column("daraja_attempts", "business_shortcode")
    op.drop_column("daraja_attempts", "daraja_environment")
