"""Persist and reconcile offline sale return events.

Revision ID: 0006_sale_return_replay
Revises: 0005_sale_replay_amounts
"""
from alembic import op
import sqlalchemy as sa

revision = "0006_sale_return_replay"
down_revision = "0005_sale_replay_amounts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("sale_items", sa.Column("returned_qty", sa.Integer(), nullable=False, server_default="0"))
    op.create_check_constraint("sale_item_returned_qty_valid", "sale_items", "returned_qty >= 0 AND returned_qty <= quantity")
    op.create_unique_constraint("uq_sale_items_tenant_shop_id", "sale_items", ["tenant_id", "shop_id", "id"])
    op.create_table(
        "sale_returns",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("shop_id", sa.Uuid(), nullable=False),
        sa.Column("transaction_id", sa.Uuid(), nullable=False),
        sa.Column("sale_id", sa.Uuid(), nullable=False),
        sa.Column("sale_item_id", sa.Uuid(), nullable=False),
        sa.Column("batch_id", sa.Uuid(), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("refund_amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("credit_reduction", sa.Numeric(12, 2), nullable=False),
        sa.Column("cash_refund", sa.Numeric(12, 2), nullable=False),
        sa.Column("reason", sa.String(300), nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("quantity > 0 AND refund_amount >= 0 AND credit_reduction >= 0 AND cash_refund >= 0 AND credit_reduction + cash_refund = refund_amount", name="sale_return_amounts_valid"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tenant_id", "shop_id", "sale_id"], ["sales.tenant_id", "sales.shop_id", "sales.id"]),
        sa.ForeignKeyConstraint(["tenant_id", "shop_id", "sale_item_id"], ["sale_items.tenant_id", "sale_items.shop_id", "sale_items.id"]),
        sa.ForeignKeyConstraint(["tenant_id", "shop_id", "batch_id"], ["medicine_batches.tenant_id", "medicine_batches.shop_id", "medicine_batches.id"]),
        sa.ForeignKeyConstraint(["actor_id"], ["users.id"]),
        sa.UniqueConstraint("tenant_id", "transaction_id", name="uq_sale_returns_tenant_transaction"),
        sa.PrimaryKeyConstraint("id"),
    )
    for column in ("tenant_id", "shop_id", "sale_id", "sale_item_id", "batch_id"):
        op.create_index(f"ix_sale_returns_{column}", "sale_returns", [column])
    op.execute(sa.text("ALTER TABLE sale_returns ENABLE ROW LEVEL SECURITY"))
    op.execute(sa.text("ALTER TABLE sale_returns FORCE ROW LEVEL SECURITY"))
    op.execute(sa.text(
        "CREATE POLICY tenant_isolation_sale_returns ON sale_returns "
        "USING (current_setting('app.tenant_id', true) IS NOT NULL "
        "AND tenant_id::text = current_setting('app.tenant_id', true)) "
        "WITH CHECK (current_setting('app.tenant_id', true) IS NOT NULL "
        "AND tenant_id::text = current_setting('app.tenant_id', true))"
    ))
    op.execute(sa.text(
        "CREATE POLICY platform_ops_sale_returns ON sale_returns "
        "USING (current_setting('app.platform_operator', true) = 'true') "
        "WITH CHECK (current_setting('app.platform_operator', true) = 'true')"
    ))


def downgrade() -> None:
    op.execute(sa.text("DROP POLICY IF EXISTS platform_ops_sale_returns ON sale_returns"))
    op.execute(sa.text("DROP POLICY IF EXISTS tenant_isolation_sale_returns ON sale_returns"))
    for column in ("tenant_id", "shop_id", "sale_id", "sale_item_id", "batch_id"):
        op.drop_index(f"ix_sale_returns_{column}", table_name="sale_returns")
    op.drop_table("sale_returns")
    op.drop_constraint("uq_sale_items_tenant_shop_id", "sale_items", type_="unique")
    op.drop_constraint("sale_item_returned_qty_valid", "sale_items", type_="check")
    op.drop_column("sale_items", "returned_qty")
