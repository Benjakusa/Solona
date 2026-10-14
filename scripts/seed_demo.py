"""Seed the local SOLONA development database with realistic demo data.

Creates (idempotently) a demo pharmacy tenant with two shops, one account
for each role, medicines, suppliers, customers, inventory batches (including
expired, expiring-soon and low-stock cases) and a short sales history.

Development only: refuses to run when APP_ENV=production. The passwords
printed by this script are shared development credentials for a private
demo - never reuse them anywhere real. Override any of them through
SEED_<ROLE>_PASSWORD environment variables (for example
SEED_OWNER_PASSWORD). Platform-operator access is intentionally NOT
granted; grant it with ``python -m app.ops grant <email>`` when needed.
"""
from __future__ import annotations

import os
import secrets
import subprocess
import sys
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"

# Keep in sync with PERMISSIONS and role_permissions in backend/app/api/v1/auth.py.
PERMISSIONS = {
    "tenant:read": "View tenant configuration and summaries",
    "tenant:manage": "Update tenant and shop configuration",
    "staff:manage": "Invite, assign, and deactivate tenant staff",
    "device:manage": "Register and revoke shop devices",
    "inventory:read": "View medicine and batch inventory",
    "inventory:write": "Manage medicine catalogue and stock receipts",
    "sales:read": "View shop sales summaries",
    "sales:write": "Record sales and payments",
    "billing:read": "View subscription and payment records",
    "billing:manage": "Initiate subscription payments and manage billing",
}
ROLE_PERMISSIONS = {
    "owner": set(PERMISSIONS),
    "administrator": set(PERMISSIONS),
    "pharmacist": {"tenant:read", "inventory:read", "inventory:write", "sales:read", "sales:write"},
    "inventory_manager": {"tenant:read", "inventory:read", "inventory:write"},
    "cashier": {"tenant:read", "inventory:read", "sales:read", "sales:write"},
}
DEFAULT_PASSWORDS = {
    "owner": "SolonaDev-Owner-2026!",
    "administrator": "SolonaDev-Admin-2026!",
    "pharmacist": "SolonaDev-Pharm-2026!",
    "inventory_manager": "SolonaDev-Stock-2026!",
    "cashier": "SolonaDev-Cash-2026!",
}
ACCOUNTS = [
    # email, full name, role, shop scope ("all" or shop codes)
    ("owner@solona.test", "Amina Owner", "owner", "all"),
    ("admin@solona.test", "Brian Administrator", "administrator", "all"),
    ("pharmacist@solona.test", "Cherie Pharmacist", "pharmacist", "all"),
    ("stock@solona.test", "Dennis Stockkeeper", "inventory_manager", ["NGR", "TWN"]),
    ("cashier@solona.test", "Esther Cashier", "cashier", ["TWN"]),
]
SHOPS = [("Ngong Road Branch", "NGR"), ("Town Branch", "TWN")]
MEDICINES = [
    # sku, name, generic, manufacturer, category, base unit, controlled
    ("PCM-500", "Paracetamol 500mg", "Paracetamol", "Atlantic", "analgesic", "tablet", False),
    ("IBU-400", "Ibuprofen 400mg", "Ibuprofen", "Cooper", "analgesic", "tablet", False),
    ("AMX-500", "Amoxicillin 500mg", "Amoxicillin", "Denk Pharma", "antibiotic", "capsule", False),
    ("AL-20-120", "Artemether/Lumefantrine 20/120mg", "Artemether", "Ajanta", "antimalarial", "tablet", False),
    ("OMP-20", "Omeprazole 20mg", "Omeprazole", "Laborate", "gastric", "capsule", False),
    ("MET-500", "Metformin 500mg", "Metformin USV", "USV", "diabetes", "tablet", False),
    ("AML-5", "Amlodipine 5mg", "Amlodipine", "Zydus", "cardiac", "tablet", False),
    ("ORS-20.5", "ORS Sachet 20.5g", "Oral rehydration salts", "UNICEF Supply", "rehydration", "sachet", False),
    ("DIC-GEL", "Diclofenac Gel 1%", "Diclofenac", "Novartis", "topical", "tube", False),
    ("DXM-SYP", "Dextromethorphan Syrup", "Dextromethorphan", "Lexicorp", "cough", "bottle", False),
    ("DZP-5", "Diazepam 5mg", "Diazepam", "ProLab", "controlled", "tablet", True),
]
SUPPLIERS = [("Kenya Medical Supplies Authority", "0720000000", "orders@kemsa.example.test"),
             ("Rift Valley Medical Distributors", "0733000000", "sales@rvmd.example.test")]
CUSTOMERS = [("Jane Wanjiku", "0711000001"), ("Peter Ochieng", "0711000002"), ("Fatuma Hassan", "0711000003")]


def deterministic_id(key: str) -> uuid.UUID:
    return uuid.uuid5(uuid.NAMESPACE_URL, f"solona.dev.seed/{key}")


def days_ago(days: int) -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=days)


def main() -> int:
    if not (BACKEND / ".env").exists():
        print("Missing backend/.env. Copy backend/.env.example and configure a PostgreSQL DATABASE_URL.", file=sys.stderr)
        return 2
    os.chdir(BACKEND)
    sys.path.insert(0, str(BACKEND))
    from app.core.config import get_settings

    settings = get_settings()
    if settings.app_env == "production":
        print("Refusing to seed demo data in production.", file=sys.stderr)
        return 2

    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], check=True)

    from app.core.security import hash_password
    from app.db.session import SessionLocal, set_tenant_context
    from app.models import (
        Device, Medicine, MedicineBatch, Permission, Purchase, Role, RolePermission,
        Sale, Shop, ShopAssignment, SubscriptionPlan, Supplier, Customer, Tenant,
        TenantMembership, TenantSubscription, User,
    )
    from app.schemas import (
        CustomerCreate, MedicineCreate, PurchaseCreate, PurchaseLineIn, SaleCreate,
        SaleLineIn, SupplierCreate,
    )
    from app.services import pharmacy as pharmacy_service
    from sqlalchemy import select

    passwords = {role: os.getenv(f"SEED_{role.upper()}_PASSWORD") or DEFAULT_PASSWORDS[role]
                 for role in DEFAULT_PASSWORDS}
    created = {"users": [], "device_credential": None}

    with SessionLocal.begin() as db:
        plan = db.scalar(select(SubscriptionPlan).where(SubscriptionPlan.code == "trial"))
        if plan is None:
            raise RuntimeError("Trial plan was not seeded by the migrations")

        permissions = {}
        for code, description in PERMISSIONS.items():
            permission = db.scalar(select(Permission).where(Permission.code == code))
            if permission is None:
                permission = Permission(code=code, description=description)
                db.add(permission)
                db.flush()
            permissions[code] = permission

        # Forced RLS on `tenants` requires the transaction-local tenant context
        # to match the new row id before the insert (same pattern as
        # auth.register_tenant). A deterministic id keeps reruns idempotent and
        # stays in sync with scripts/start_demo.py.
        tenant_id = deterministic_id("tenant:solona-demo")
        set_tenant_context(db, tenant_id)
        tenant = db.scalar(select(Tenant).where(Tenant.slug == "solona-demo"))
        if tenant is None:
            tenant = Tenant(id=tenant_id, name="Solona Demo Pharmacy", slug="solona-demo")
            db.add(tenant)
            db.flush()
        set_tenant_context(db, tenant.id)

        shops = {}
        for name, code in SHOPS:
            shop = db.scalar(select(Shop).where(Shop.tenant_id == tenant.id, Shop.code == code))
            if shop is None:
                shop = Shop(tenant_id=tenant.id, name=name, code=code)
                db.add(shop)
                db.flush()
            shops[code] = shop

        roles = {}
        for role_name, permission_codes in ROLE_PERMISSIONS.items():
            role = db.scalar(select(Role).where(Role.tenant_id == tenant.id, Role.name == role_name))
            if role is None:
                role = Role(tenant_id=tenant.id, name=role_name, is_system=True)
                db.add(role)
                db.flush()
            roles[role_name] = role
            existing = {
                row[0] for row in db.execute(
                    select(Permission.code).join(RolePermission, RolePermission.permission_id == Permission.id)
                    .where(RolePermission.tenant_id == tenant.id, RolePermission.role_id == role.id)
                )
            }
            for code in permission_codes - existing:
                db.add(RolePermission(tenant_id=tenant.id, role_id=role.id,
                                      permission_id=permissions[code].id))

        subscription = db.scalar(select(TenantSubscription).where(TenantSubscription.tenant_id == tenant.id))
        now = datetime.now(timezone.utc)
        if subscription is None:
            db.add(TenantSubscription(tenant_id=tenant.id, plan_id=plan.id, status="trialing",
                                      starts_at=now, ends_at=now + timedelta(days=30)))
        elif subscription.status not in {"trialing", "active"} or subscription.ends_at < now + timedelta(days=7):
            subscription.status = "trialing"
            subscription.starts_at = now
            subscription.ends_at = now + timedelta(days=30)
            subscription.grace_until = None

        for email, full_name, role_name, scope in ACCOUNTS:
            user = db.scalar(select(User).where(User.email == email))
            if user is None:
                user = User(email=email, full_name=full_name,
                            password_hash=hash_password(passwords[role_name]))
                db.add(user)
                db.flush()
                created["users"].append(email)
            elif os.getenv(f"SEED_{role_name.upper()}_PASSWORD"):
                user.password_hash = hash_password(passwords[role_name])
            membership = db.scalar(select(TenantMembership).where(
                TenantMembership.tenant_id == tenant.id, TenantMembership.user_id == user.id))
            if membership is None:
                membership = TenantMembership(tenant_id=tenant.id, user_id=user.id,
                                              role_id=roles[role_name].id,
                                              all_shops=scope == "all")
                db.add(membership)
                db.flush()
            else:
                membership.role_id = roles[role_name].id
                membership.all_shops = scope == "all"
                membership.is_active = True
            if scope != "all":
                assigned = {assignment.shop_id for assignment in db.scalars(
                    select(ShopAssignment).where(ShopAssignment.membership_id == membership.id))}
                for code in scope:
                    if shops[code].id not in assigned:
                        db.add(ShopAssignment(tenant_id=tenant.id, shop_id=shops[code].id,
                                              membership_id=membership.id))

        medicines = {}
        for sku, name, generic, maker, category, unit, controlled in MEDICINES:
            medicine = db.scalar(select(Medicine).where(Medicine.tenant_id == tenant.id, Medicine.sku == sku))
            if medicine is None:
                medicine = Medicine(tenant_id=tenant.id, sku=sku, name=name, generic_name=generic,
                                    manufacturer=maker, category=category, base_unit=unit,
                                    is_controlled=controlled)
                db.add(medicine)
                db.flush()
            medicines[sku] = medicine

        suppliers = {}
        for name, phone, email in SUPPLIERS:
            supplier = db.scalar(select(Supplier).where(Supplier.tenant_id == tenant.id, Supplier.name == name))
            if supplier is None:
                supplier = pharmacy_service.add_supplier(db, tenant.id, user.id, SupplierCreate(
                    name=name, phone=phone, email=email))
            suppliers[name] = supplier

        customers = {}
        for name, phone in CUSTOMERS:
            customer = db.scalar(select(Customer).where(Customer.tenant_id == tenant.id, Customer.name == name))
            if customer is None:
                customer = pharmacy_service.add_customer(db, tenant.id, user.id, CustomerCreate(
                    name=name, phone=phone))
            customers[name] = customer

        # Stock receipts and a short sales history (the docstring contract).
        # The guards keep reruns idempotent; purchases and sales commit in this
        # same transaction, so batches exist only when the sales do too.
        if db.scalar(select(MedicineBatch.id).where(
                MedicineBatch.tenant_id == tenant.id).limit(1)) is None:
            purchase_plan = [
                ("NGR", "Kenya Medical Supplies Authority", [
                    ("PCM-500", "PCM-NGR-01", 540, 2000, "2.50", "5.00"),
                    ("IBU-400", "IBU-NGR-01", 500, 800, "3.00", "6.00"),
                    ("AMX-500", "AMX-NGR-01", 400, 300, "8.00", "15.00"),
                    # Expiring soon: exercises the expiry alert on the dashboard.
                    ("OMP-20", "OMP-NGR-01", 25, 120, "6.00", "12.00"),
                    # Already expired: cannot be sold, only flagged.
                    ("MET-500", "MET-NGR-01", -10, 90, "5.00", "10.00"),
                ]),
                ("TWN", "Rift Valley Medical Distributors", [
                    ("PCM-500", "PCM-TWN-01", 450, 1000, "2.50", "5.50"),
                    ("AL-20-120", "AL-TWN-01", 600, 400, "12.00", "25.00"),
                    # Small receipt plus the sale below leaves this batch low stock.
                    ("AML-5", "AML-TWN-01", 365, 30, "4.00", "8.00"),
                    ("DZP-5", "DZP-TWN-01", 700, 200, "5.00", "10.00"),
                ]),
            ]
            for code, supplier_name, lines in purchase_plan:
                shop = shops[code]
                supplier = suppliers[supplier_name]
                items = [
                    PurchaseLineIn(
                        medicine_id=medicines[sku].id,
                        batch_no=batch_no,
                        expiry_date=(datetime.now(timezone.utc) + timedelta(days=days)).date(),
                        quantity=quantity,
                        purchase_cost=Decimal(cost),
                        selling_price=Decimal(price),
                    )
                    for sku, batch_no, days, quantity, cost, price in lines
                ]
                pharmacy_service.receive_purchase(db, tenant.id, shop.id, user.id, PurchaseCreate(
                    shop_id=shop.id, supplier_id=supplier.id,
                    transaction_id=deterministic_id(f"purchase/{code}"),
                    reference=f"SEED-{code}", items=items))

        sku_by_medicine = {medicine.id: sku for sku, medicine in medicines.items()}
        batches = {
            (batch.shop_id, sku_by_medicine[batch.medicine_id]): batch
            for batch in db.scalars(select(MedicineBatch).where(MedicineBatch.tenant_id == tenant.id))
        }
        if db.scalar(select(Sale.id).where(Sale.tenant_id == tenant.id).limit(1)) is None:
            sale_plan = [
                # shop code, customer name or None, [(sku, quantity)], days ago
                ("NGR", "Jane Wanjiku", [("PCM-500", 10)], 3),
                ("NGR", "Peter Ochieng", [("AMX-500", 5), ("IBU-400", 20)], 1),
                ("TWN", "Fatuma Hassan", [("AL-20-120", 4)], 2),
                ("TWN", None, [("AML-5", 25), ("PCM-500", 15)], 0),
            ]
            for index, (code, customer_name, lines, days_back) in enumerate(sale_plan, start=1):
                shop = shops[code]
                items = []
                total = Decimal("0.00")
                for sku, quantity in lines:
                    batch = batches[(shop.id, sku)]
                    items.append(SaleLineIn(batch_id=batch.id, quantity=quantity))
                    total += batch.selling_price * quantity
                customer = customers.get(customer_name) if customer_name else None
                sale = pharmacy_service.record_sale(db, tenant.id, shop.id, user.id, SaleCreate(
                    shop_id=shop.id,
                    customer_id=customer.id if customer else None,
                    transaction_id=deterministic_id(f"sale/{code}/{index}"),
                    invoice_no=f"INV-SEED-{code}-{index}",
                    items=items,
                    payment_method="cash",
                    amount_paid=total,
                ))
                # Backdate so the dashboard shows a short sales history.
                sale.created_at = days_ago(days_back)


if __name__ == "__main__":
    raise SystemExit(main())

