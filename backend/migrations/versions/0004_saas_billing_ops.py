"""Add subscription billing, Daraja attempts, and restricted platform operations.

Revision ID: 0004_saas_billing_ops
Revises: 0003_sync_event_sequence
"""
from alembic import op
import sqlalchemy as sa

revision = "0004_saas_billing_ops"
down_revision = "0003_sync_event_sequence"
branch_labels = None
depends_on = None


def _tenant_policy(table: str, *, callback: bool = False) -> None:
    callback_clause = " OR current_setting('app.daraja_callback', true) = 'true'" if callback else ""
    op.execute(sa.text(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY'))
    op.execute(sa.text(f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY'))
    op.execute(sa.text(
        f'CREATE POLICY tenant_isolation_{table} ON "{table}" '
        f'USING ((current_setting(\'app.tenant_id\', true) IS NOT NULL AND tenant_id::text = current_setting(\'app.tenant_id\', true)){callback_clause}) '
        f'WITH CHECK ((current_setting(\'app.tenant_id\', true) IS NOT NULL AND tenant_id::text = current_setting(\'app.tenant_id\', true)){callback_clause})'
    ))


def upgrade() -> None:
    op.add_column("subscription_plans", sa.Column("monthly_price", sa.Numeric(12, 2), nullable=False, server_default="0"))
    op.add_column("subscription_plans", sa.Column("trial_days", sa.Integer(), nullable=False, server_default="0"))
    op.execute(sa.text("UPDATE subscription_plans SET trial_days=14 WHERE code='trial'"))
    op.create_unique_constraint("uq_tenant_subscriptions_tenant_id_id", "tenant_subscriptions", ["tenant_id", "id"])
    op.create_table(
        "subscription_payments",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("subscription_id", sa.Uuid(), nullable=False),
        sa.Column("idempotency_key", sa.String(80), nullable=False),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False, server_default="KES"),
        sa.Column("method", sa.String(24), nullable=False, server_default="mpesa_stk"),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("account_reference", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tenant_id", "subscription_id"], ["tenant_subscriptions.tenant_id", "tenant_subscriptions.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("tenant_id", "id"),
        sa.UniqueConstraint("tenant_id", "idempotency_key"),
        sa.CheckConstraint("amount > 0", name="subscription_payment_amount_positive"),
        sa.CheckConstraint("status IN ('pending','paid','failed','cancelled','reconciliation_required')", name="subscription_payment_status_valid"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_subscription_payments_tenant_id", "subscription_payments", ["tenant_id"])
    op.create_index("ix_subscription_payments_subscription_id", "subscription_payments", ["subscription_id"])
    op.create_table(
        "daraja_attempts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("subscription_payment_id", sa.Uuid(), nullable=False),
        sa.Column("checkout_request_id", sa.String(120)),
        sa.Column("merchant_request_id", sa.String(120)),
        sa.Column("phone_number", sa.String(16), nullable=False),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("result_code", sa.String(32)),
        sa.Column("result_description", sa.String(300)),
        sa.Column("mpesa_receipt", sa.String(40)),
        sa.Column("callback_digest", sa.String(64)),
        sa.Column("callback_payload", sa.JSON()),
        sa.Column("callback_received_at", sa.DateTime(timezone=True)),
        sa.Column("conversation_id", sa.String(120)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tenant_id", "subscription_payment_id"], ["subscription_payments.tenant_id", "subscription_payments.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("tenant_id", "checkout_request_id"),
        sa.UniqueConstraint("tenant_id", "merchant_request_id"),
        sa.UniqueConstraint("tenant_id", "mpesa_receipt"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_daraja_attempts_tenant_id", "daraja_attempts", ["tenant_id"])
    op.create_index("ix_daraja_attempts_subscription_payment_id", "daraja_attempts", ["subscription_payment_id"])
    op.create_index("ix_daraja_pending", "daraja_attempts", ["status", "created_at"])
    op.add_column("daraja_attempts", sa.Column("callback_count", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("daraja_attempts", sa.Column("last_callback_digest", sa.String(64), nullable=True))
    op.create_table(
        "platform_operators",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id"),
    )
    op.create_table(
        "platform_audit_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid()),
        sa.Column("action", sa.String(100), nullable=False),
        sa.Column("resource_type", sa.String(80), nullable=False),
        sa.Column("resource_id", sa.Uuid()),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["actor_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_platform_audit_time", "platform_audit_events", ["created_at"])
    op.create_table(
        "support_access_grants",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("operator_id", sa.Uuid(), nullable=False),
        sa.Column("reason", sa.String(500), nullable=False),
        sa.Column("granted_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["operator_id"], ["users.id"]),
        sa.CheckConstraint("expires_at > granted_at", name="support_grant_expiry_valid"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_support_access_grants_tenant_id", "support_access_grants", ["tenant_id"])
    op.create_table(
        "support_incidents",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("subject", sa.String(180), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default="open"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_support_incidents_tenant_id", "support_incidents", ["tenant_id"])
    op.create_index("ix_support_incident_status", "support_incidents", ["status", "created_at"])

    bind = op.get_bind()
    bind.execute(sa.text("INSERT INTO permissions(code, description) VALUES ('billing:read','View subscription and payment records'), ('billing:manage','Initiate subscription payments and manage billing') ON CONFLICT (code) DO NOTHING"))
    bind.execute(sa.text("INSERT INTO role_permissions(tenant_id, role_id, permission_id) SELECT r.tenant_id, r.id, p.id FROM roles r CROSS JOIN permissions p WHERE r.name='owner' AND p.code IN ('billing:read','billing:manage') ON CONFLICT (tenant_id, role_id, permission_id) DO NOTHING"))

    for table in ("subscription_payments", "daraja_attempts", "support_access_grants", "support_incidents"):
        _tenant_policy(table, callback=(table == "daraja_attempts"))
    for table in ("tenants", "shops", "devices", "tenant_subscriptions", "audit_events", "sync_events", "support_incidents", "support_access_grants", "subscription_payments", "daraja_attempts"):
        op.execute(sa.text(f'CREATE POLICY platform_ops_{table} ON "{table}" USING (current_setting(\'app.platform_operator\', true) = \'true\') WITH CHECK (current_setting(\'app.platform_operator\', true) = \'true\')'))


def downgrade() -> None:
    op.drop_column("daraja_attempts", "last_callback_digest")
    op.drop_column("daraja_attempts", "callback_count")
    for table in ("tenants", "shops", "devices", "tenant_subscriptions", "audit_events", "sync_events", "support_incidents", "support_access_grants", "subscription_payments", "daraja_attempts"):
        op.execute(sa.text(f'DROP POLICY IF EXISTS platform_ops_{table} ON "{table}"'))
    for table in ("support_incidents", "support_access_grants", "daraja_attempts", "subscription_payments"):
        op.execute(sa.text(f'DROP POLICY IF EXISTS tenant_isolation_{table} ON "{table}"'))
        op.drop_table(table)
    op.drop_table("platform_operators")
    op.drop_index("ix_platform_audit_time", table_name="platform_audit_events")
    op.drop_table("platform_audit_events")
    op.drop_constraint("uq_tenant_subscriptions_tenant_id_id", "tenant_subscriptions", type_="unique")
    op.drop_column("subscription_plans", "trial_days")
    op.drop_column("subscription_plans", "monthly_price")
    op.execute(sa.text("DELETE FROM role_permissions WHERE permission_id IN (SELECT id FROM permissions WHERE code IN ('billing:read','billing:manage'))"))
    op.execute(sa.text("DELETE FROM permissions WHERE code IN ('billing:read','billing:manage')"))
