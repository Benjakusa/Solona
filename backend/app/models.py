import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger, Boolean, CheckConstraint, Date, DateTime, ForeignKey, ForeignKeyConstraint,
    Identity, Index, Integer, JSON, Numeric, String, Text, UniqueConstraint, Uuid, func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)


class Tenant(Base, TimestampMixin):
    __tablename__ = "tenants"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    slug: Mapped[str] = mapped_column(String(80), nullable=False, unique=True)
    business_details: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class Shop(Base, TimestampMixin):
    __tablename__ = "shops"
    __table_args__ = (UniqueConstraint("tenant_id", "code"), UniqueConstraint("tenant_id", "id"))
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    code: Mapped[str] = mapped_column(String(40), nullable=False)
    timezone: Mapped[str] = mapped_column(String(80), default="Africa/Nairobi", nullable=False)
    receipt_settings: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class User(Base, TimestampMixin):
    __tablename__ = "users"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(300), nullable=False)
    full_name: Mapped[str] = mapped_column(String(160), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class Permission(Base):
    __tablename__ = "permissions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(80), unique=True, nullable=False)
    description: Mapped[str] = mapped_column(String(200), nullable=False)


class Role(Base):
    __tablename__ = "roles"
    __table_args__ = (UniqueConstraint("tenant_id", "name"), UniqueConstraint("tenant_id", "id"))
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    is_system: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class RolePermission(Base):
    __tablename__ = "role_permissions"
    __table_args__ = (UniqueConstraint("tenant_id", "role_id", "permission_id"), ForeignKeyConstraint(["tenant_id", "role_id"], ["roles.tenant_id", "roles.id"], ondelete="CASCADE"))
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    role_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    permission_id: Mapped[int] = mapped_column(Integer, ForeignKey("permissions.id", ondelete="CASCADE"), nullable=False)


class TenantMembership(Base, TimestampMixin):
    __tablename__ = "tenant_memberships"
    __table_args__ = (UniqueConstraint("tenant_id", "id"), UniqueConstraint("tenant_id", "user_id"), ForeignKeyConstraint(["tenant_id", "role_id"], ["roles.tenant_id", "roles.id"]))
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    role_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    all_shops: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class ShopAssignment(Base):
    __tablename__ = "shop_assignments"
    __table_args__ = (UniqueConstraint("tenant_id", "shop_id", "membership_id"), ForeignKeyConstraint(["tenant_id", "shop_id"], ["shops.tenant_id", "shops.id"], ondelete="CASCADE"), ForeignKeyConstraint(["tenant_id", "membership_id"], ["tenant_memberships.tenant_id", "tenant_memberships.id"], ondelete="CASCADE"))
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    shop_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    membership_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)


class Device(Base, TimestampMixin):
    __tablename__ = "devices"
    __table_args__ = (UniqueConstraint("tenant_id", "shop_id", "id"), UniqueConstraint("tenant_id", "id"), ForeignKeyConstraint(["tenant_id", "shop_id"], ["shops.tenant_id", "shops.id"], ondelete="CASCADE"))
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    shop_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    platform: Mapped[str] = mapped_column(String(80), nullable=False)
    credential_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    registered_by: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id"), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SubscriptionPlan(Base):
    __tablename__ = "subscription_plans"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    code: Mapped[str] = mapped_column(String(40), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    max_shops: Mapped[int] = mapped_column(Integer, nullable=False)
    max_devices_per_shop: Mapped[int] = mapped_column(Integer, nullable=False)
    monthly_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"), nullable=False)
    trial_days: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    features: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class TenantSubscription(Base, TimestampMixin):
    __tablename__ = "tenant_subscriptions"
    __table_args__ = (UniqueConstraint("tenant_id", "id"), CheckConstraint("status IN ('trialing','active','past_due','canceled','expired')", name="subscription_status_valid"), Index("ix_subscription_tenant_period", "tenant_id", "status", "ends_at"))
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    plan_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("subscription_plans.id"), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    grace_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SubscriptionPayment(Base, TimestampMixin):
    __tablename__ = "subscription_payments"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id"),
        UniqueConstraint("tenant_id", "idempotency_key"),
        CheckConstraint("amount > 0", name="subscription_payment_amount_positive"),
        CheckConstraint("status IN ('pending','paid','failed','cancelled','reconciliation_required')", name="subscription_payment_status_valid"),
        ForeignKeyConstraint(["tenant_id", "subscription_id"], ["tenant_subscriptions.tenant_id", "tenant_subscriptions.id"], ondelete="CASCADE"),
    )
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    subscription_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    idempotency_key: Mapped[str] = mapped_column(String(80), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="KES", nullable=False)
    method: Mapped[str] = mapped_column(String(24), default="mpesa_stk", nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="pending", nullable=False)
    account_reference: Mapped[str] = mapped_column(String(32), nullable=False)


class DarajaAttempt(Base, TimestampMixin):
    __tablename__ = "daraja_attempts"
    __table_args__ = (
        UniqueConstraint("tenant_id", "checkout_request_id"),
        UniqueConstraint("tenant_id", "merchant_request_id"),
        UniqueConstraint("tenant_id", "mpesa_receipt"),
        ForeignKeyConstraint(["tenant_id", "subscription_payment_id"], ["subscription_payments.tenant_id", "subscription_payments.id"], ondelete="CASCADE"),
        Index("ix_daraja_pending", "status", "created_at"),
    )
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    subscription_payment_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    checkout_request_id: Mapped[str | None] = mapped_column(String(120))
    merchant_request_id: Mapped[str | None] = mapped_column(String(120))
    phone_number: Mapped[str] = mapped_column(String(16), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    daraja_environment: Mapped[str | None] = mapped_column(String(16))
    business_shortcode: Mapped[str | None] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(32), default="pending", nullable=False)
    result_code: Mapped[str | None] = mapped_column(String(32))
    result_description: Mapped[str | None] = mapped_column(String(300))
    mpesa_receipt: Mapped[str | None] = mapped_column(String(40))
    callback_digest: Mapped[str | None] = mapped_column(String(64))
    # Preserve every delivery (including conflicting repeats) for reconciliation.
    callback_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_callback_digest: Mapped[str | None] = mapped_column(String(64))
    callback_payload: Mapped[dict | None] = mapped_column(JSON)
    callback_received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    conversation_id: Mapped[str | None] = mapped_column(String(120))


class PlatformOperator(Base):
    __tablename__ = "platform_operators"
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class PlatformAuditEvent(Base):
    __tablename__ = "platform_audit_events"
    __table_args__ = (Index("ix_platform_audit_time", "created_at"),)
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    actor_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id"), nullable=False)
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("tenants.id", ondelete="SET NULL"))
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(80), nullable=False)
    resource_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    details: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class SupportAccessGrant(Base):
    __tablename__ = "support_access_grants"
    __table_args__ = (CheckConstraint("expires_at > granted_at", name="support_grant_expiry_valid"),)
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    operator_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id"), nullable=False)
    reason: Mapped[str] = mapped_column(String(500), nullable=False)
    granted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SupportIncident(Base):
    __tablename__ = "support_incidents"
    __table_args__ = (Index("ix_support_incident_status", "status", "created_at"),)
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    created_by: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id"), nullable=False)
    subject: Mapped[str] = mapped_column(String(180), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="open", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)


class Medicine(Base, TimestampMixin):
    __tablename__ = "medicines"
    __table_args__ = (UniqueConstraint("tenant_id", "sku"), UniqueConstraint("tenant_id", "id"))
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    sku: Mapped[str] = mapped_column(String(80), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    generic_name: Mapped[str | None] = mapped_column(String(200))
    manufacturer: Mapped[str | None] = mapped_column(String(160))
    barcode: Mapped[str | None] = mapped_column(String(100), index=True)
    category: Mapped[str | None] = mapped_column(String(100))
    base_unit: Mapped[str] = mapped_column(String(30), default="unit", nullable=False)
    is_controlled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class Supplier(Base, TimestampMixin):
    __tablename__ = "suppliers"
    __table_args__ = (UniqueConstraint("tenant_id", "id"), CheckConstraint("payable_balance >= 0", name="supplier_payable_nonnegative"))
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(180), nullable=False)
    phone: Mapped[str | None] = mapped_column(String(40))
    email: Mapped[str | None] = mapped_column(String(320))
    payable_balance: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class Customer(Base, TimestampMixin):
    __tablename__ = "customers"
    __table_args__ = (UniqueConstraint("tenant_id", "id"), CheckConstraint("credit_balance >= 0", name="customer_credit_nonnegative"))
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(180), nullable=False)
    phone: Mapped[str | None] = mapped_column(String(40))
    credit_balance: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class Purchase(Base, TimestampMixin):
    __tablename__ = "purchases"
    __table_args__ = (UniqueConstraint("tenant_id", "id"), UniqueConstraint("tenant_id", "shop_id", "id"), UniqueConstraint("tenant_id", "transaction_id"), ForeignKeyConstraint(["tenant_id", "shop_id"], ["shops.tenant_id", "shops.id"]), ForeignKeyConstraint(["tenant_id", "supplier_id"], ["suppliers.tenant_id", "suppliers.id"]), CheckConstraint("total_amount >= 0 AND amount_paid >= 0 AND amount_paid <= total_amount", name="purchase_amounts_valid"))
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    shop_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    supplier_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    transaction_id: Mapped[uuid.UUID] = mapped_column(Uuid, default=uuid.uuid4, nullable=False)
    reference: Mapped[str | None] = mapped_column(String(100))
    total_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"), nullable=False)
    amount_paid: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"), nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="received", nullable=False)
    purchased_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class PurchaseItem(Base):
    __tablename__ = "purchase_items"
    __table_args__ = (ForeignKeyConstraint(["tenant_id", "shop_id", "purchase_id"], ["purchases.tenant_id", "purchases.shop_id", "purchases.id"], ondelete="CASCADE"), ForeignKeyConstraint(["tenant_id", "medicine_id"], ["medicines.tenant_id", "medicines.id"]), ForeignKeyConstraint(["tenant_id", "shop_id", "medicine_id", "batch_id"], ["medicine_batches.tenant_id", "medicine_batches.shop_id", "medicine_batches.medicine_id", "medicine_batches.id"]), UniqueConstraint("tenant_id", "id"), CheckConstraint("quantity > 0", name="purchase_quantity_positive"), CheckConstraint("purchase_cost >= 0 AND selling_price >= 0", name="purchase_prices_nonnegative"))
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    purchase_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    shop_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    medicine_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    batch_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    batch_no: Mapped[str] = mapped_column(String(100), nullable=False)
    expiry_date: Mapped[date] = mapped_column(Date, nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    purchase_cost: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    selling_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)


class MedicineBatch(Base, TimestampMixin):
    __tablename__ = "medicine_batches"
    __table_args__ = (UniqueConstraint("tenant_id", "shop_id", "medicine_id", "batch_no"), UniqueConstraint("tenant_id", "shop_id", "id"), UniqueConstraint("tenant_id", "shop_id", "medicine_id", "id"), ForeignKeyConstraint(["tenant_id", "shop_id"], ["shops.tenant_id", "shops.id"], ondelete="CASCADE"), ForeignKeyConstraint(["tenant_id", "medicine_id"], ["medicines.tenant_id", "medicines.id"]), ForeignKeyConstraint(["tenant_id", "supplier_id"], ["suppliers.tenant_id", "suppliers.id"]))
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    shop_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    medicine_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    batch_no: Mapped[str] = mapped_column(String(100), nullable=False)
    expiry_date: Mapped[date] = mapped_column(Date, nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    purchase_cost: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    selling_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    supplier_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    __table_args__ += (CheckConstraint("quantity >= 0", name="batch_quantity_nonnegative"), CheckConstraint("purchase_cost >= 0 AND selling_price >= 0", name="batch_prices_nonnegative"), Index("ix_batches_shop_expiry", "tenant_id", "shop_id", "expiry_date"))


class Sale(Base, TimestampMixin):
    __tablename__ = "sales"
    __table_args__ = (UniqueConstraint("tenant_id", "id"), UniqueConstraint("tenant_id", "shop_id", "id"), UniqueConstraint("tenant_id", "transaction_id"), ForeignKeyConstraint(["tenant_id", "shop_id"], ["shops.tenant_id", "shops.id"]), ForeignKeyConstraint(["tenant_id", "customer_id"], ["customers.tenant_id", "customers.id"]), CheckConstraint("total_amount >= 0 AND amount_paid >= 0 AND amount_paid <= total_amount", name="sale_amounts_valid"))
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    shop_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    transaction_id: Mapped[uuid.UUID] = mapped_column(Uuid, default=uuid.uuid4, nullable=False)
    cashier_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id"), nullable=False)
    customer_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, index=True)
    invoice_no: Mapped[str] = mapped_column(String(80), nullable=False)
    sold_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="KES", nullable=False)
    total_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    discount_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"), nullable=False)
    tax_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"), nullable=False)
    amount_paid: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"), nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="completed", nullable=False)
    __table_args__ += (UniqueConstraint("tenant_id", "shop_id", "invoice_no"), Index("ix_sales_shop_sold", "tenant_id", "shop_id", "sold_at"))


class SaleItem(Base):
    __tablename__ = "sale_items"
    __table_args__ = (UniqueConstraint("tenant_id", "shop_id", "id", name="uq_sale_items_tenant_shop_id"), ForeignKeyConstraint(["tenant_id", "shop_id", "sale_id"], ["sales.tenant_id", "sales.shop_id", "sales.id"], ondelete="CASCADE"), ForeignKeyConstraint(["tenant_id", "shop_id", "batch_id"], ["medicine_batches.tenant_id", "medicine_batches.shop_id", "medicine_batches.id"]), CheckConstraint("quantity > 0", name="sale_quantity_positive"), CheckConstraint("unit_price >= 0 AND unit_cost >= 0 AND subtotal >= 0", name="sale_prices_nonnegative"), CheckConstraint("returned_qty >= 0 AND returned_qty <= quantity", name="sale_item_returned_qty_valid"))
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    sale_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    shop_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    batch_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    unit_cost: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    subtotal: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    returned_qty: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class SaleReturn(Base):
    __tablename__ = "sale_returns"
    __table_args__ = (
        UniqueConstraint("tenant_id", "transaction_id", name="uq_sale_returns_tenant_transaction"),
        ForeignKeyConstraint(["tenant_id", "shop_id", "sale_id"], ["sales.tenant_id", "sales.shop_id", "sales.id"]),
        ForeignKeyConstraint(["tenant_id", "shop_id", "sale_item_id"], ["sale_items.tenant_id", "sale_items.shop_id", "sale_items.id"]),
        ForeignKeyConstraint(["tenant_id", "shop_id", "batch_id"], ["medicine_batches.tenant_id", "medicine_batches.shop_id", "medicine_batches.id"]),
        CheckConstraint("quantity > 0 AND refund_amount >= 0 AND credit_reduction >= 0 AND cash_refund >= 0 AND credit_reduction + cash_refund = refund_amount", name="sale_return_amounts_valid"),
    )
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    shop_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    transaction_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    sale_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    sale_item_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    batch_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    refund_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    credit_reduction: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    cash_refund: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    reason: Mapped[str] = mapped_column(String(300), nullable=False)
    actor_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class Payment(Base, TimestampMixin):
    __tablename__ = "payments"
    __table_args__ = (UniqueConstraint("tenant_id", "transaction_id"), UniqueConstraint("tenant_id", "provider_reference"), ForeignKeyConstraint(["tenant_id", "shop_id"], ["shops.tenant_id", "shops.id"]), ForeignKeyConstraint(["tenant_id", "shop_id", "sale_id"], ["sales.tenant_id", "sales.shop_id", "sales.id"]), ForeignKeyConstraint(["tenant_id", "shop_id", "purchase_id"], ["purchases.tenant_id", "purchases.shop_id", "purchases.id"]), CheckConstraint("amount > 0", name="payment_amount_positive"), CheckConstraint("(sale_id IS NOT NULL) <> (purchase_id IS NOT NULL)", name="payment_one_document"))
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    shop_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    sale_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, index=True)
    purchase_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, index=True)
    transaction_id: Mapped[uuid.UUID] = mapped_column(Uuid, default=uuid.uuid4, nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="KES", nullable=False)
    method: Mapped[str] = mapped_column(String(30), nullable=False)
    provider_reference: Mapped[str | None] = mapped_column(String(160))
    status: Mapped[str] = mapped_column(String(24), default="completed", nullable=False)


class StockMovement(Base):
    __tablename__ = "stock_movements"
    __table_args__ = (UniqueConstraint("tenant_id", "transaction_id", "batch_id", "movement_type"), ForeignKeyConstraint(["tenant_id", "shop_id", "batch_id"], ["medicine_batches.tenant_id", "medicine_batches.shop_id", "medicine_batches.id"]), CheckConstraint("quantity_delta != 0", name="movement_nonzero"), Index("ix_stock_movements_batch_time", "tenant_id", "shop_id", "batch_id", "created_at"))
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    shop_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    batch_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    transaction_id: Mapped[uuid.UUID] = mapped_column(Uuid, default=uuid.uuid4, nullable=False)
    movement_type: Mapped[str] = mapped_column(String(30), nullable=False)
    quantity_delta: Mapped[int] = mapped_column(Integer, nullable=False)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class AuditEvent(Base):
    __tablename__ = "audit_events"
    __table_args__ = (ForeignKeyConstraint(["tenant_id", "shop_id"], ["shops.tenant_id", "shops.id"]),)
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    shop_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, index=True)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id"))
    transaction_id: Mapped[uuid.UUID] = mapped_column(Uuid, default=uuid.uuid4, nullable=False)
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(80), nullable=False)
    resource_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    details: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class SyncEvent(Base):
    __tablename__ = "sync_events"
    __table_args__ = (UniqueConstraint("tenant_id", "event_id"), ForeignKeyConstraint(["tenant_id", "shop_id"], ["shops.tenant_id", "shops.id"]), ForeignKeyConstraint(["tenant_id", "shop_id", "device_id"], ["devices.tenant_id", "devices.shop_id", "devices.id"]), Index("ix_sync_events_shop_created", "tenant_id", "shop_id", "created_at"))
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    sequence: Mapped[int] = mapped_column(BigInteger, Identity(), unique=True, nullable=False, index=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    shop_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    device_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    event_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    transaction_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    event_type: Mapped[str] = mapped_column(String(80), nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="pending", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class AuthSession(Base):
    __tablename__ = "auth_sessions"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    family_id: Mapped[uuid.UUID] = mapped_column(Uuid, default=uuid.uuid4, nullable=False, index=True)
    refresh_token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    replaced_by_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("auth_sessions.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class UserInvitation(Base):
    __tablename__ = "user_invitations"
    __table_args__ = (ForeignKeyConstraint(["tenant_id", "role_id"], ["roles.tenant_id", "roles.id"]),)
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    role_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    shop_ids: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
