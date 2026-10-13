import re
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator


Email = Annotated[str, Field(min_length=3, max_length=320)]
Password = Annotated[str, Field(min_length=12, max_length=256)]
Money = Annotated[Decimal, Field(gt=0, max_digits=12, decimal_places=2)]


def validate_email(value: str) -> str:
    value = value.strip().lower()
    if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value):
        raise ValueError("A valid email address is required")
    return value


class RegisterTenant(BaseModel):
    tenant_name: Annotated[str, Field(min_length=2, max_length=160)]
    tenant_slug: Annotated[str, Field(pattern=r"^[a-z0-9](?:[a-z0-9-]{1,78}[a-z0-9])?$")]
    shop_name: Annotated[str, Field(min_length=2, max_length=160)]
    shop_code: Annotated[str, Field(min_length=1, max_length=40, pattern=r"^[A-Za-z0-9_-]+$")]
    owner_email: Email
    owner_name: Annotated[str, Field(min_length=2, max_length=160)]
    owner_password: Password

    @field_validator("owner_email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        return validate_email(value)


class LoginRequest(BaseModel):
    email: Email
    password: Annotated[str, Field(min_length=1, max_length=256)]

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        return validate_email(value)


class RefreshRequest(BaseModel):
    refresh_token: Annotated[str, Field(min_length=40, max_length=200)]


class StkPushRequest(BaseModel):
    phone_number: Annotated[str, Field(min_length=9, max_length=16)]
    idempotency_key: Annotated[str, Field(min_length=8, max_length=80, pattern=r"^[A-Za-z0-9_-]+$")]


class SupportIncidentCreate(BaseModel):
    subject: Annotated[str, Field(min_length=4, max_length=180)]
    description: Annotated[str, Field(min_length=10, max_length=4000)]


class SupportAccessRequest(BaseModel):
    reason: Annotated[str, Field(min_length=12, max_length=500)]
    duration_minutes: Annotated[int, Field(ge=5, le=60)] = 30


class PlatformSubscriptionPatch(BaseModel):
    status: Annotated[str, Field(pattern=r"^(trialing|active|past_due|canceled|expired)$")]
    grace_days: Annotated[int, Field(ge=0, le=30)] = 0
    end_days: Annotated[int, Field(ge=1, le=366)] | None = None
    plan_id: UUID | None = None


class MembershipRolePatch(BaseModel):
    role: Annotated[str, Field(min_length=1, max_length=80)]


class SubscriptionPlanCreate(BaseModel):
    code: Annotated[str, Field(min_length=2, max_length=40, pattern=r"^[a-z0-9_-]+$")]
    name: Annotated[str, Field(min_length=2, max_length=100)]
    max_shops: Annotated[int, Field(ge=1, le=1000)]
    max_devices_per_shop: Annotated[int, Field(ge=1, le=1000)]
    monthly_price: Annotated[Decimal, Field(ge=0, max_digits=12, decimal_places=2)]
    trial_days: Annotated[int, Field(ge=0, le=90)] = 0
    features: dict[str, bool] = Field(default_factory=dict)


class SubscriptionPlanPatch(BaseModel):
    name: Annotated[str, Field(min_length=2, max_length=100)] | None = None
    max_shops: Annotated[int, Field(ge=1, le=1000)] | None = None
    max_devices_per_shop: Annotated[int, Field(ge=1, le=1000)] | None = None
    monthly_price: Annotated[Decimal, Field(ge=0, max_digits=12, decimal_places=2)] | None = None
    trial_days: Annotated[int, Field(ge=0, le=90)] | None = None
    features: dict[str, bool] | None = None
    is_active: bool | None = None


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int


class TenantRegistrationResponse(TokenResponse):
    tenant_id: UUID
    shop_id: UUID


class TenantPatch(BaseModel):
    name: Annotated[str, Field(min_length=2, max_length=160)] | None = None
    business_details: dict[str, str] | None = None


class ShopCreate(BaseModel):
    name: Annotated[str, Field(min_length=2, max_length=160)]
    code: Annotated[str, Field(min_length=1, max_length=40, pattern=r"^[A-Za-z0-9_-]+$")]
    timezone: Annotated[str, Field(min_length=1, max_length=80)] = "Africa/Nairobi"
    receipt_settings: dict[str, str | int | bool] = Field(default_factory=dict)

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError:
            raise ValueError("Unknown timezone") from None
        return value


class ShopPatch(BaseModel):
    name: Annotated[str, Field(min_length=2, max_length=160)] | None = None
    timezone: Annotated[str, Field(min_length=1, max_length=80)] | None = None
    receipt_settings: dict[str, str | int | bool] | None = None
    is_active: bool | None = None

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value: str | None) -> str | None:
        if value is not None:
            try:
                ZoneInfo(value)
            except ZoneInfoNotFoundError:
                raise ValueError("Unknown timezone") from None
        return value


class InvitationCreate(BaseModel):
    email: Email
    role: Annotated[str, Field(min_length=1, max_length=80)]
    shop_ids: list[UUID] = Field(default_factory=list, max_length=100)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        return validate_email(value)


class InvitationAccept(BaseModel):
    token: Annotated[str, Field(min_length=40, max_length=200)]
    full_name: Annotated[str, Field(min_length=2, max_length=160)]
    password: Password | None = None


class DeviceCreate(BaseModel):
    shop_id: UUID
    name: Annotated[str, Field(min_length=1, max_length=120)]
    platform: Annotated[str, Field(min_length=1, max_length=80)]


class MedicineCreate(BaseModel):
    sku: Annotated[str, Field(min_length=1, max_length=80)]
    name: Annotated[str, Field(min_length=1, max_length=200)]
    generic_name: Annotated[str, Field(max_length=200)] | None = None
    manufacturer: Annotated[str, Field(max_length=160)] | None = None
    barcode: Annotated[str, Field(max_length=100)] | None = None
    category: Annotated[str, Field(max_length=100)] | None = None
    base_unit: Annotated[str, Field(min_length=1, max_length=30)] = "unit"
    is_controlled: bool = False


class PurchaseLineIn(BaseModel):
    medicine_id: UUID
    batch_no: Annotated[str, Field(min_length=1, max_length=100)]
    expiry_date: date
    quantity: Annotated[int, Field(gt=0, le=1_000_000)]
    purchase_cost: Money
    selling_price: Money


class PurchaseCreate(BaseModel):
    shop_id: UUID
    supplier_id: UUID
    transaction_id: UUID
    reference: Annotated[str, Field(max_length=100)] | None = None
    amount_paid: Annotated[Decimal, Field(ge=0, max_digits=12, decimal_places=2)] | None = None
    payment_method: Annotated[str, Field(pattern=r"^(cash|card|bank_transfer|credit|other)$")] = "cash"
    items: Annotated[list[PurchaseLineIn], Field(min_length=1, max_length=500)]


class SaleLineIn(BaseModel):
    batch_id: UUID
    quantity: Annotated[int, Field(gt=0, le=1_000_000)]


class SaleCreate(BaseModel):
    shop_id: UUID
    customer_id: UUID | None = None
    transaction_id: UUID
    invoice_no: Annotated[str, Field(min_length=1, max_length=80)]
    currency: Annotated[str, Field(pattern=r"^[A-Z]{3}$")] = "KES"
    items: Annotated[list[SaleLineIn], Field(min_length=1, max_length=500)]
    payment_method: Annotated[str, Field(pattern=r"^(cash|card|bank_transfer|credit|other)$")]
    amount_paid: Annotated[Decimal, Field(ge=0, max_digits=12, decimal_places=2)]


class SummaryResponse(BaseModel):
    tenant_id: UUID
    shop_id: UUID | None = None
    shop_count: int
    active_users: int
    sales_count: int
    sales_total: Decimal
    units_in_stock: int


class SupplierCreate(BaseModel):
    name: Annotated[str, Field(min_length=1, max_length=180)]
    phone: Annotated[str, Field(max_length=40)] | None = None
    email: Email | None = None

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str | None) -> str | None:
        return validate_email(value) if value else None


class CustomerCreate(BaseModel):
    name: Annotated[str, Field(min_length=1, max_length=180)]
    phone: Annotated[str, Field(max_length=40)] | None = None


class PaymentCreate(BaseModel):
    transaction_id: UUID
    amount: Money
    method: Annotated[str, Field(pattern=r"^(cash|card|bank_transfer|credit|other)$")]
    provider_reference: Annotated[str, Field(max_length=160)] | None = None


class OkResponse(BaseModel):
    ok: bool = True


class SyncEventIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    event_id: UUID
    transaction_id: UUID
    tenant_id: UUID
    shop_id: UUID
    device_id: UUID
    event_type: Annotated[str, Field(min_length=1, max_length=80)]
    aggregate_id: Annotated[str, Field(min_length=1, max_length=100)]
    schema_version: Annotated[int, Field(ge=1, le=100)] = 1
    occurred_at: datetime
    payload: dict


class SyncPushIn(BaseModel):
    events: Annotated[list[SyncEventIn], Field(min_length=1, max_length=100)]
