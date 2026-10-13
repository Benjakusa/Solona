"""Initial tenant, identity, inventory and transaction schema.

Revision ID: 0001_initial
Revises:
"""
import uuid

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None

TRIAL_PLAN_ID = "f7fcf669-d951-4f2b-9c7b-6d522fba1950"
PERMISSIONS = [
    ('tenant:read', 'View tenant configuration and summaries'),
    ('tenant:manage', 'Update tenant and shop configuration'),
    ('staff:manage', 'Invite, assign, and deactivate tenant staff'),
    ('device:manage', 'Register and revoke shop devices'),
    ('inventory:read', 'View medicine and batch inventory'),
    ('inventory:write', 'Manage medicine catalogue and stock receipts'),
    ('sales:read', 'View shop sales summaries'),
    ('sales:write', 'Record sales and payments'),
]

CREATE_TABLES = [
    'CREATE TABLE permissions (\n\tid SERIAL NOT NULL, \n\tcode VARCHAR(80) NOT NULL, \n\tdescription VARCHAR(200) NOT NULL, \n\tCONSTRAINT pk_permissions PRIMARY KEY (id), \n\tCONSTRAINT uq_permissions_code UNIQUE (code)\n)',
    'CREATE TABLE subscription_plans (\n\tid UUID NOT NULL, \n\tcode VARCHAR(40) NOT NULL, \n\tname VARCHAR(100) NOT NULL, \n\tmax_shops INTEGER NOT NULL, \n\tmax_devices_per_shop INTEGER NOT NULL, \n\tfeatures JSON NOT NULL, \n\tis_active BOOLEAN NOT NULL, \n\tCONSTRAINT pk_subscription_plans PRIMARY KEY (id), \n\tCONSTRAINT uq_subscription_plans_code UNIQUE (code)\n)',
    'CREATE TABLE tenants (\n\tid UUID NOT NULL, \n\tname VARCHAR(160) NOT NULL, \n\tslug VARCHAR(80) NOT NULL, \n\tbusiness_details JSON NOT NULL, \n\tis_active BOOLEAN NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tupdated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tCONSTRAINT pk_tenants PRIMARY KEY (id), \n\tCONSTRAINT uq_tenants_slug UNIQUE (slug)\n)',
    'CREATE TABLE users (\n\tid UUID NOT NULL, \n\temail VARCHAR(320) NOT NULL, \n\tpassword_hash VARCHAR(300) NOT NULL, \n\tfull_name VARCHAR(160) NOT NULL, \n\tis_active BOOLEAN NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tupdated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tCONSTRAINT pk_users PRIMARY KEY (id)\n)',
    'CREATE TABLE auth_sessions (\n\tid UUID NOT NULL, \n\tuser_id UUID NOT NULL, \n\tfamily_id UUID NOT NULL, \n\trefresh_token_hash VARCHAR(64) NOT NULL, \n\texpires_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\trevoked_at TIMESTAMP WITH TIME ZONE, \n\treplaced_by_id UUID, \n\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tCONSTRAINT pk_auth_sessions PRIMARY KEY (id), \n\tCONSTRAINT fk_auth_sessions_user_id_users FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE, \n\tCONSTRAINT uq_auth_sessions_refresh_token_hash UNIQUE (refresh_token_hash), \n\tCONSTRAINT fk_auth_sessions_replaced_by_id_auth_sessions FOREIGN KEY(replaced_by_id) REFERENCES auth_sessions (id)\n)',
    'CREATE TABLE customers (\n\tid UUID NOT NULL, \n\ttenant_id UUID NOT NULL, \n\tname VARCHAR(180) NOT NULL, \n\tphone VARCHAR(40), \n\tcredit_balance NUMERIC(12, 2) NOT NULL, \n\tis_active BOOLEAN NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tupdated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tCONSTRAINT pk_customers PRIMARY KEY (id), \n\tCONSTRAINT uq_customers_tenant_id_id UNIQUE (tenant_id, id), \n\tCONSTRAINT ck_customers_customer_credit_nonnegative CHECK (credit_balance >= 0), \n\tCONSTRAINT fk_customers_tenant_id_tenants FOREIGN KEY(tenant_id) REFERENCES tenants (id) ON DELETE CASCADE\n)',
    'CREATE TABLE medicines (\n\tid UUID NOT NULL, \n\ttenant_id UUID NOT NULL, \n\tsku VARCHAR(80) NOT NULL, \n\tname VARCHAR(200) NOT NULL, \n\tgeneric_name VARCHAR(200), \n\tmanufacturer VARCHAR(160), \n\tbarcode VARCHAR(100), \n\tcategory VARCHAR(100), \n\tbase_unit VARCHAR(30) NOT NULL, \n\tis_controlled BOOLEAN NOT NULL, \n\tis_active BOOLEAN NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tupdated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tCONSTRAINT pk_medicines PRIMARY KEY (id), \n\tCONSTRAINT uq_medicines_tenant_id_sku UNIQUE (tenant_id, sku), \n\tCONSTRAINT uq_medicines_tenant_id_id UNIQUE (tenant_id, id), \n\tCONSTRAINT fk_medicines_tenant_id_tenants FOREIGN KEY(tenant_id) REFERENCES tenants (id) ON DELETE CASCADE\n)',
    'CREATE TABLE roles (\n\tid UUID NOT NULL, \n\ttenant_id UUID NOT NULL, \n\tname VARCHAR(80) NOT NULL, \n\tis_system BOOLEAN NOT NULL, \n\tCONSTRAINT pk_roles PRIMARY KEY (id), \n\tCONSTRAINT uq_roles_tenant_id_name UNIQUE (tenant_id, name), \n\tCONSTRAINT uq_roles_tenant_id_id UNIQUE (tenant_id, id), \n\tCONSTRAINT fk_roles_tenant_id_tenants FOREIGN KEY(tenant_id) REFERENCES tenants (id) ON DELETE CASCADE\n)',
    'CREATE TABLE shops (\n\tid UUID NOT NULL, \n\ttenant_id UUID NOT NULL, \n\tname VARCHAR(160) NOT NULL, \n\tcode VARCHAR(40) NOT NULL, \n\ttimezone VARCHAR(80) NOT NULL, \n\treceipt_settings JSON NOT NULL, \n\tis_active BOOLEAN NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tupdated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tCONSTRAINT pk_shops PRIMARY KEY (id), \n\tCONSTRAINT uq_shops_tenant_id_code UNIQUE (tenant_id, code), \n\tCONSTRAINT uq_shops_tenant_id_id UNIQUE (tenant_id, id), \n\tCONSTRAINT fk_shops_tenant_id_tenants FOREIGN KEY(tenant_id) REFERENCES tenants (id) ON DELETE CASCADE\n)',
    'CREATE TABLE suppliers (\n\tid UUID NOT NULL, \n\ttenant_id UUID NOT NULL, \n\tname VARCHAR(180) NOT NULL, \n\tphone VARCHAR(40), \n\temail VARCHAR(320), \n\tpayable_balance NUMERIC(12, 2) NOT NULL, \n\tis_active BOOLEAN NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tupdated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tCONSTRAINT pk_suppliers PRIMARY KEY (id), \n\tCONSTRAINT uq_suppliers_tenant_id_id UNIQUE (tenant_id, id), \n\tCONSTRAINT ck_suppliers_supplier_payable_nonnegative CHECK (payable_balance >= 0), \n\tCONSTRAINT fk_suppliers_tenant_id_tenants FOREIGN KEY(tenant_id) REFERENCES tenants (id) ON DELETE CASCADE\n)',
    "CREATE TABLE tenant_subscriptions (\n\tid UUID NOT NULL, \n\ttenant_id UUID NOT NULL, \n\tplan_id UUID NOT NULL, \n\tstatus VARCHAR(24) NOT NULL, \n\tstarts_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tends_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tgrace_until TIMESTAMP WITH TIME ZONE, \n\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tupdated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tCONSTRAINT pk_tenant_subscriptions PRIMARY KEY (id), \n\tCONSTRAINT ck_tenant_subscriptions_subscription_status_valid CHECK (status IN ('trialing','active','past_due','canceled','expired')), \n\tCONSTRAINT fk_tenant_subscriptions_tenant_id_tenants FOREIGN KEY(tenant_id) REFERENCES tenants (id) ON DELETE CASCADE, \n\tCONSTRAINT fk_tenant_subscriptions_plan_id_subscription_plans FOREIGN KEY(plan_id) REFERENCES subscription_plans (id)\n)",
    'CREATE TABLE audit_events (\n\tid UUID NOT NULL, \n\ttenant_id UUID NOT NULL, \n\tshop_id UUID, \n\tactor_id UUID, \n\ttransaction_id UUID NOT NULL, \n\taction VARCHAR(100) NOT NULL, \n\tresource_type VARCHAR(80) NOT NULL, \n\tresource_id UUID, \n\tdetails JSON NOT NULL, \n\toccurred_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tCONSTRAINT pk_audit_events PRIMARY KEY (id), \n\tCONSTRAINT fk_audit_events_tenant_id_shops FOREIGN KEY(tenant_id, shop_id) REFERENCES shops (tenant_id, id), \n\tCONSTRAINT fk_audit_events_tenant_id_tenants FOREIGN KEY(tenant_id) REFERENCES tenants (id) ON DELETE CASCADE, \n\tCONSTRAINT fk_audit_events_actor_id_users FOREIGN KEY(actor_id) REFERENCES users (id)\n)',
    'CREATE TABLE devices (\n\tid UUID NOT NULL, \n\ttenant_id UUID NOT NULL, \n\tshop_id UUID NOT NULL, \n\tname VARCHAR(120) NOT NULL, \n\tplatform VARCHAR(80) NOT NULL, \n\tcredential_hash VARCHAR(64) NOT NULL, \n\tregistered_by UUID NOT NULL, \n\trevoked_at TIMESTAMP WITH TIME ZONE, \n\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tupdated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tCONSTRAINT pk_devices PRIMARY KEY (id), \n\tCONSTRAINT uq_devices_tenant_id_shop_id_id UNIQUE (tenant_id, shop_id, id), \n\tCONSTRAINT uq_devices_tenant_id_id UNIQUE (tenant_id, id), \n\tCONSTRAINT fk_devices_tenant_id_shops FOREIGN KEY(tenant_id, shop_id) REFERENCES shops (tenant_id, id) ON DELETE CASCADE, \n\tCONSTRAINT fk_devices_tenant_id_tenants FOREIGN KEY(tenant_id) REFERENCES tenants (id) ON DELETE CASCADE, \n\tCONSTRAINT fk_devices_registered_by_users FOREIGN KEY(registered_by) REFERENCES users (id)\n)',
    'CREATE TABLE medicine_batches (\n\tid UUID NOT NULL, \n\ttenant_id UUID NOT NULL, \n\tshop_id UUID NOT NULL, \n\tmedicine_id UUID NOT NULL, \n\tbatch_no VARCHAR(100) NOT NULL, \n\texpiry_date DATE NOT NULL, \n\tquantity INTEGER NOT NULL, \n\tpurchase_cost NUMERIC(12, 2) NOT NULL, \n\tselling_price NUMERIC(12, 2) NOT NULL, \n\tsupplier_id UUID, \n\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tupdated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tCONSTRAINT pk_medicine_batches PRIMARY KEY (id), \n\tCONSTRAINT uq_medicine_batches_tenant_id_shop_id_medicine_id_batch_no UNIQUE (tenant_id, shop_id, medicine_id, batch_no), \n\tCONSTRAINT uq_medicine_batches_tenant_id_shop_id_id UNIQUE (tenant_id, shop_id, id), \n\tCONSTRAINT uq_medicine_batches_tenant_id_shop_id_medicine_id_id UNIQUE (tenant_id, shop_id, medicine_id, id), \n\tCONSTRAINT fk_medicine_batches_tenant_id_shops FOREIGN KEY(tenant_id, shop_id) REFERENCES shops (tenant_id, id) ON DELETE CASCADE, \n\tCONSTRAINT fk_medicine_batches_tenant_id_medicines FOREIGN KEY(tenant_id, medicine_id) REFERENCES medicines (tenant_id, id), \n\tCONSTRAINT fk_medicine_batches_tenant_id_suppliers FOREIGN KEY(tenant_id, supplier_id) REFERENCES suppliers (tenant_id, id), \n\tCONSTRAINT ck_medicine_batches_batch_quantity_nonnegative CHECK (quantity >= 0), \n\tCONSTRAINT ck_medicine_batches_batch_prices_nonnegative CHECK (purchase_cost >= 0 AND selling_price >= 0), \n\tCONSTRAINT fk_medicine_batches_tenant_id_tenants FOREIGN KEY(tenant_id) REFERENCES tenants (id) ON DELETE CASCADE\n)',
    'CREATE TABLE purchases (\n\tid UUID NOT NULL, \n\ttenant_id UUID NOT NULL, \n\tshop_id UUID NOT NULL, \n\tsupplier_id UUID NOT NULL, \n\ttransaction_id UUID NOT NULL, \n\treference VARCHAR(100), \n\ttotal_amount NUMERIC(12, 2) NOT NULL, \n\tamount_paid NUMERIC(12, 2) NOT NULL, \n\tstatus VARCHAR(24) NOT NULL, \n\tpurchased_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tupdated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tCONSTRAINT pk_purchases PRIMARY KEY (id), \n\tCONSTRAINT uq_purchases_tenant_id_id UNIQUE (tenant_id, id), \n\tCONSTRAINT uq_purchases_tenant_id_shop_id_id UNIQUE (tenant_id, shop_id, id), \n\tCONSTRAINT uq_purchases_tenant_id_transaction_id UNIQUE (tenant_id, transaction_id), \n\tCONSTRAINT fk_purchases_tenant_id_shops FOREIGN KEY(tenant_id, shop_id) REFERENCES shops (tenant_id, id), \n\tCONSTRAINT fk_purchases_tenant_id_suppliers FOREIGN KEY(tenant_id, supplier_id) REFERENCES suppliers (tenant_id, id), \n\tCONSTRAINT ck_purchases_purchase_amounts_valid CHECK (total_amount >= 0 AND amount_paid >= 0 AND amount_paid <= total_amount), \n\tCONSTRAINT fk_purchases_tenant_id_tenants FOREIGN KEY(tenant_id) REFERENCES tenants (id) ON DELETE CASCADE\n)',
    'CREATE TABLE role_permissions (\n\tid SERIAL NOT NULL, \n\ttenant_id UUID NOT NULL, \n\trole_id UUID NOT NULL, \n\tpermission_id INTEGER NOT NULL, \n\tCONSTRAINT pk_role_permissions PRIMARY KEY (id), \n\tCONSTRAINT uq_role_permissions_tenant_id_role_id_permission_id UNIQUE (tenant_id, role_id, permission_id), \n\tCONSTRAINT fk_role_permissions_tenant_id_roles FOREIGN KEY(tenant_id, role_id) REFERENCES roles (tenant_id, id) ON DELETE CASCADE, \n\tCONSTRAINT fk_role_permissions_tenant_id_tenants FOREIGN KEY(tenant_id) REFERENCES tenants (id) ON DELETE CASCADE, \n\tCONSTRAINT fk_role_permissions_permission_id_permissions FOREIGN KEY(permission_id) REFERENCES permissions (id) ON DELETE CASCADE\n)',
    'CREATE TABLE sales (\n\tid UUID NOT NULL, \n\ttenant_id UUID NOT NULL, \n\tshop_id UUID NOT NULL, \n\ttransaction_id UUID NOT NULL, \n\tcashier_id UUID NOT NULL, \n\tcustomer_id UUID, \n\tinvoice_no VARCHAR(80) NOT NULL, \n\tsold_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tcurrency VARCHAR(3) NOT NULL, \n\ttotal_amount NUMERIC(12, 2) NOT NULL, \n\tamount_paid NUMERIC(12, 2) NOT NULL, \n\tstatus VARCHAR(24) NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tupdated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tCONSTRAINT pk_sales PRIMARY KEY (id), \n\tCONSTRAINT uq_sales_tenant_id_id UNIQUE (tenant_id, id), \n\tCONSTRAINT uq_sales_tenant_id_shop_id_id UNIQUE (tenant_id, shop_id, id), \n\tCONSTRAINT uq_sales_tenant_id_transaction_id UNIQUE (tenant_id, transaction_id), \n\tCONSTRAINT fk_sales_tenant_id_shops FOREIGN KEY(tenant_id, shop_id) REFERENCES shops (tenant_id, id), \n\tCONSTRAINT fk_sales_tenant_id_customers FOREIGN KEY(tenant_id, customer_id) REFERENCES customers (tenant_id, id), \n\tCONSTRAINT ck_sales_sale_amounts_valid CHECK (total_amount >= 0 AND amount_paid >= 0 AND amount_paid <= total_amount), \n\tCONSTRAINT uq_sales_tenant_id_shop_id_invoice_no UNIQUE (tenant_id, shop_id, invoice_no), \n\tCONSTRAINT fk_sales_tenant_id_tenants FOREIGN KEY(tenant_id) REFERENCES tenants (id) ON DELETE CASCADE, \n\tCONSTRAINT fk_sales_cashier_id_users FOREIGN KEY(cashier_id) REFERENCES users (id)\n)',
    'CREATE TABLE tenant_memberships (\n\tid UUID NOT NULL, \n\ttenant_id UUID NOT NULL, \n\tuser_id UUID NOT NULL, \n\trole_id UUID NOT NULL, \n\tall_shops BOOLEAN NOT NULL, \n\tis_active BOOLEAN NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tupdated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tCONSTRAINT pk_tenant_memberships PRIMARY KEY (id), \n\tCONSTRAINT uq_tenant_memberships_tenant_id_id UNIQUE (tenant_id, id), \n\tCONSTRAINT uq_tenant_memberships_tenant_id_user_id UNIQUE (tenant_id, user_id), \n\tCONSTRAINT fk_tenant_memberships_tenant_id_roles FOREIGN KEY(tenant_id, role_id) REFERENCES roles (tenant_id, id), \n\tCONSTRAINT fk_tenant_memberships_tenant_id_tenants FOREIGN KEY(tenant_id) REFERENCES tenants (id) ON DELETE CASCADE, \n\tCONSTRAINT fk_tenant_memberships_user_id_users FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE\n)',
    'CREATE TABLE user_invitations (\n\tid UUID NOT NULL, \n\ttenant_id UUID NOT NULL, \n\temail VARCHAR(320) NOT NULL, \n\trole_id UUID NOT NULL, \n\tshop_ids JSON NOT NULL, \n\ttoken_hash VARCHAR(64) NOT NULL, \n\texpires_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\taccepted_at TIMESTAMP WITH TIME ZONE, \n\tcreated_by UUID NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tCONSTRAINT pk_user_invitations PRIMARY KEY (id), \n\tCONSTRAINT fk_user_invitations_tenant_id_roles FOREIGN KEY(tenant_id, role_id) REFERENCES roles (tenant_id, id), \n\tCONSTRAINT fk_user_invitations_tenant_id_tenants FOREIGN KEY(tenant_id) REFERENCES tenants (id) ON DELETE CASCADE, \n\tCONSTRAINT uq_user_invitations_token_hash UNIQUE (token_hash), \n\tCONSTRAINT fk_user_invitations_created_by_users FOREIGN KEY(created_by) REFERENCES users (id)\n)',
    'CREATE TABLE payments (\n\tid UUID NOT NULL, \n\ttenant_id UUID NOT NULL, \n\tshop_id UUID NOT NULL, \n\tsale_id UUID, \n\tpurchase_id UUID, \n\ttransaction_id UUID NOT NULL, \n\tamount NUMERIC(12, 2) NOT NULL, \n\tcurrency VARCHAR(3) NOT NULL, \n\tmethod VARCHAR(30) NOT NULL, \n\tprovider_reference VARCHAR(160), \n\tstatus VARCHAR(24) NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tupdated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tCONSTRAINT pk_payments PRIMARY KEY (id), \n\tCONSTRAINT uq_payments_tenant_id_transaction_id UNIQUE (tenant_id, transaction_id), \n\tCONSTRAINT uq_payments_tenant_id_provider_reference UNIQUE (tenant_id, provider_reference), \n\tCONSTRAINT fk_payments_tenant_id_shops FOREIGN KEY(tenant_id, shop_id) REFERENCES shops (tenant_id, id), \n\tCONSTRAINT fk_payments_tenant_id_sales FOREIGN KEY(tenant_id, shop_id, sale_id) REFERENCES sales (tenant_id, shop_id, id), \n\tCONSTRAINT fk_payments_tenant_id_purchases FOREIGN KEY(tenant_id, shop_id, purchase_id) REFERENCES purchases (tenant_id, shop_id, id), \n\tCONSTRAINT ck_payments_payment_amount_positive CHECK (amount > 0), \n\tCONSTRAINT ck_payments_payment_one_document CHECK ((sale_id IS NOT NULL) <> (purchase_id IS NOT NULL)), \n\tCONSTRAINT fk_payments_tenant_id_tenants FOREIGN KEY(tenant_id) REFERENCES tenants (id) ON DELETE CASCADE\n)',
    'CREATE TABLE purchase_items (\n\tid UUID NOT NULL, \n\ttenant_id UUID NOT NULL, \n\tpurchase_id UUID NOT NULL, \n\tshop_id UUID NOT NULL, \n\tmedicine_id UUID NOT NULL, \n\tbatch_id UUID NOT NULL, \n\tbatch_no VARCHAR(100) NOT NULL, \n\texpiry_date DATE NOT NULL, \n\tquantity INTEGER NOT NULL, \n\tpurchase_cost NUMERIC(12, 2) NOT NULL, \n\tselling_price NUMERIC(12, 2) NOT NULL, \n\tCONSTRAINT pk_purchase_items PRIMARY KEY (id), \n\tCONSTRAINT fk_purchase_items_tenant_id_purchases FOREIGN KEY(tenant_id, shop_id, purchase_id) REFERENCES purchases (tenant_id, shop_id, id) ON DELETE CASCADE, \n\tCONSTRAINT fk_purchase_items_tenant_id_medicines FOREIGN KEY(tenant_id, medicine_id) REFERENCES medicines (tenant_id, id), \n\tCONSTRAINT fk_purchase_items_tenant_id_medicine_batches FOREIGN KEY(tenant_id, shop_id, medicine_id, batch_id) REFERENCES medicine_batches (tenant_id, shop_id, medicine_id, id), \n\tCONSTRAINT uq_purchase_items_tenant_id_id UNIQUE (tenant_id, id), \n\tCONSTRAINT ck_purchase_items_purchase_quantity_positive CHECK (quantity > 0), \n\tCONSTRAINT ck_purchase_items_purchase_prices_nonnegative CHECK (purchase_cost >= 0 AND selling_price >= 0), \n\tCONSTRAINT fk_purchase_items_tenant_id_tenants FOREIGN KEY(tenant_id) REFERENCES tenants (id) ON DELETE CASCADE\n)',
    'CREATE TABLE sale_items (\n\tid UUID NOT NULL, \n\ttenant_id UUID NOT NULL, \n\tsale_id UUID NOT NULL, \n\tshop_id UUID NOT NULL, \n\tbatch_id UUID NOT NULL, \n\tquantity INTEGER NOT NULL, \n\tunit_price NUMERIC(12, 2) NOT NULL, \n\tunit_cost NUMERIC(12, 2) NOT NULL, \n\tsubtotal NUMERIC(12, 2) NOT NULL, \n\tCONSTRAINT pk_sale_items PRIMARY KEY (id), \n\tCONSTRAINT fk_sale_items_tenant_id_sales FOREIGN KEY(tenant_id, shop_id, sale_id) REFERENCES sales (tenant_id, shop_id, id) ON DELETE CASCADE, \n\tCONSTRAINT fk_sale_items_tenant_id_medicine_batches FOREIGN KEY(tenant_id, shop_id, batch_id) REFERENCES medicine_batches (tenant_id, shop_id, id), \n\tCONSTRAINT ck_sale_items_sale_quantity_positive CHECK (quantity > 0), \n\tCONSTRAINT ck_sale_items_sale_prices_nonnegative CHECK (unit_price >= 0 AND unit_cost >= 0 AND subtotal >= 0), \n\tCONSTRAINT fk_sale_items_tenant_id_tenants FOREIGN KEY(tenant_id) REFERENCES tenants (id) ON DELETE CASCADE\n)',
    'CREATE TABLE shop_assignments (\n\tid UUID NOT NULL, \n\ttenant_id UUID NOT NULL, \n\tshop_id UUID NOT NULL, \n\tmembership_id UUID NOT NULL, \n\tCONSTRAINT pk_shop_assignments PRIMARY KEY (id), \n\tCONSTRAINT uq_shop_assignments_tenant_id_shop_id_membership_id UNIQUE (tenant_id, shop_id, membership_id), \n\tCONSTRAINT fk_shop_assignments_tenant_id_shops FOREIGN KEY(tenant_id, shop_id) REFERENCES shops (tenant_id, id) ON DELETE CASCADE, \n\tCONSTRAINT fk_shop_assignments_tenant_id_tenant_memberships FOREIGN KEY(tenant_id, membership_id) REFERENCES tenant_memberships (tenant_id, id) ON DELETE CASCADE, \n\tCONSTRAINT fk_shop_assignments_tenant_id_tenants FOREIGN KEY(tenant_id) REFERENCES tenants (id) ON DELETE CASCADE\n)',
    'CREATE TABLE stock_movements (\n\tid UUID NOT NULL, \n\ttenant_id UUID NOT NULL, \n\tshop_id UUID NOT NULL, \n\tbatch_id UUID NOT NULL, \n\ttransaction_id UUID NOT NULL, \n\tmovement_type VARCHAR(30) NOT NULL, \n\tquantity_delta INTEGER NOT NULL, \n\tactor_id UUID, \n\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tCONSTRAINT pk_stock_movements PRIMARY KEY (id), \n\tCONSTRAINT uq_stock_movements_tenant_id_transaction_id_batch_id_mo_2766 UNIQUE (tenant_id, transaction_id, batch_id, movement_type), \n\tCONSTRAINT fk_stock_movements_tenant_id_medicine_batches FOREIGN KEY(tenant_id, shop_id, batch_id) REFERENCES medicine_batches (tenant_id, shop_id, id), \n\tCONSTRAINT ck_stock_movements_movement_nonzero CHECK (quantity_delta != 0), \n\tCONSTRAINT fk_stock_movements_tenant_id_tenants FOREIGN KEY(tenant_id) REFERENCES tenants (id) ON DELETE CASCADE, \n\tCONSTRAINT fk_stock_movements_actor_id_users FOREIGN KEY(actor_id) REFERENCES users (id)\n)',
    'CREATE TABLE sync_events (\n\tid UUID NOT NULL, \n\ttenant_id UUID NOT NULL, \n\tshop_id UUID NOT NULL, \n\tdevice_id UUID NOT NULL, \n\tevent_id UUID NOT NULL, \n\ttransaction_id UUID NOT NULL, \n\tevent_type VARCHAR(80) NOT NULL, \n\tschema_version INTEGER NOT NULL, \n\tpayload JSON NOT NULL, \n\tstatus VARCHAR(24) NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tCONSTRAINT pk_sync_events PRIMARY KEY (id), \n\tCONSTRAINT uq_sync_events_tenant_id_event_id UNIQUE (tenant_id, event_id), \n\tCONSTRAINT fk_sync_events_tenant_id_shops FOREIGN KEY(tenant_id, shop_id) REFERENCES shops (tenant_id, id), \n\tCONSTRAINT fk_sync_events_tenant_id_devices FOREIGN KEY(tenant_id, shop_id, device_id) REFERENCES devices (tenant_id, shop_id, id), \n\tCONSTRAINT fk_sync_events_tenant_id_tenants FOREIGN KEY(tenant_id) REFERENCES tenants (id) ON DELETE CASCADE\n)',
]

CREATE_INDEXES = [
    'CREATE UNIQUE INDEX ix_users_email ON users (email)',
    'CREATE INDEX ix_auth_sessions_family_id ON auth_sessions (family_id)',
    'CREATE INDEX ix_auth_sessions_user_id ON auth_sessions (user_id)',
    'CREATE INDEX ix_customers_tenant_id ON customers (tenant_id)',
    'CREATE INDEX ix_medicines_barcode ON medicines (barcode)',
    'CREATE INDEX ix_medicines_tenant_id ON medicines (tenant_id)',
    'CREATE INDEX ix_roles_tenant_id ON roles (tenant_id)',
    'CREATE INDEX ix_shops_tenant_id ON shops (tenant_id)',
    'CREATE INDEX ix_suppliers_tenant_id ON suppliers (tenant_id)',
    'CREATE INDEX ix_subscription_tenant_period ON tenant_subscriptions (tenant_id, status, ends_at)',
    'CREATE INDEX ix_tenant_subscriptions_tenant_id ON tenant_subscriptions (tenant_id)',
    'CREATE INDEX ix_audit_events_shop_id ON audit_events (shop_id)',
    'CREATE INDEX ix_audit_events_tenant_id ON audit_events (tenant_id)',
    'CREATE INDEX ix_devices_shop_id ON devices (shop_id)',
    'CREATE INDEX ix_devices_tenant_id ON devices (tenant_id)',
    'CREATE INDEX ix_batches_shop_expiry ON medicine_batches (tenant_id, shop_id, expiry_date)',
    'CREATE INDEX ix_medicine_batches_medicine_id ON medicine_batches (medicine_id)',
    'CREATE INDEX ix_medicine_batches_shop_id ON medicine_batches (shop_id)',
    'CREATE INDEX ix_medicine_batches_tenant_id ON medicine_batches (tenant_id)',
    'CREATE INDEX ix_purchases_shop_id ON purchases (shop_id)',
    'CREATE INDEX ix_purchases_supplier_id ON purchases (supplier_id)',
    'CREATE INDEX ix_purchases_tenant_id ON purchases (tenant_id)',
    'CREATE INDEX ix_role_permissions_tenant_id ON role_permissions (tenant_id)',
    'CREATE INDEX ix_sales_customer_id ON sales (customer_id)',
    'CREATE INDEX ix_sales_shop_id ON sales (shop_id)',
    'CREATE INDEX ix_sales_shop_sold ON sales (tenant_id, shop_id, sold_at)',
    'CREATE INDEX ix_sales_tenant_id ON sales (tenant_id)',
    'CREATE INDEX ix_tenant_memberships_tenant_id ON tenant_memberships (tenant_id)',
    'CREATE INDEX ix_tenant_memberships_user_id ON tenant_memberships (user_id)',
    'CREATE INDEX ix_user_invitations_tenant_id ON user_invitations (tenant_id)',
    'CREATE INDEX ix_payments_purchase_id ON payments (purchase_id)',
    'CREATE INDEX ix_payments_sale_id ON payments (sale_id)',
    'CREATE INDEX ix_payments_shop_id ON payments (shop_id)',
    'CREATE INDEX ix_payments_tenant_id ON payments (tenant_id)',
    'CREATE INDEX ix_purchase_items_batch_id ON purchase_items (batch_id)',
    'CREATE INDEX ix_purchase_items_medicine_id ON purchase_items (medicine_id)',
    'CREATE INDEX ix_purchase_items_purchase_id ON purchase_items (purchase_id)',
    'CREATE INDEX ix_purchase_items_tenant_id ON purchase_items (tenant_id)',
    'CREATE INDEX ix_sale_items_batch_id ON sale_items (batch_id)',
    'CREATE INDEX ix_sale_items_sale_id ON sale_items (sale_id)',
    'CREATE INDEX ix_sale_items_tenant_id ON sale_items (tenant_id)',
    'CREATE INDEX ix_shop_assignments_membership_id ON shop_assignments (membership_id)',
    'CREATE INDEX ix_shop_assignments_shop_id ON shop_assignments (shop_id)',
    'CREATE INDEX ix_shop_assignments_tenant_id ON shop_assignments (tenant_id)',
    'CREATE INDEX ix_stock_movements_batch_id ON stock_movements (batch_id)',
    'CREATE INDEX ix_stock_movements_batch_time ON stock_movements (tenant_id, shop_id, batch_id, created_at)',
    'CREATE INDEX ix_stock_movements_shop_id ON stock_movements (shop_id)',
    'CREATE INDEX ix_stock_movements_tenant_id ON stock_movements (tenant_id)',
    'CREATE INDEX ix_sync_events_device_id ON sync_events (device_id)',
    'CREATE INDEX ix_sync_events_shop_created ON sync_events (tenant_id, shop_id, created_at)',
    'CREATE INDEX ix_sync_events_shop_id ON sync_events (shop_id)',
    'CREATE INDEX ix_sync_events_tenant_id ON sync_events (tenant_id)',
]

TENANT_TABLES = [
    'customers',
    'medicines',
    'roles',
    'shops',
    'suppliers',
    'tenant_subscriptions',
    'audit_events',
    'devices',
    'medicine_batches',
    'purchases',
    'role_permissions',
    'sales',
    'tenant_memberships',
    'user_invitations',
    'payments',
    'purchase_items',
    'sale_items',
    'shop_assignments',
    'stock_movements',
    'sync_events',
    'tenants',
]

DROP_TABLES = [
    'sync_events',
    'stock_movements',
    'shop_assignments',
    'sale_items',
    'purchase_items',
    'payments',
    'user_invitations',
    'tenant_memberships',
    'sales',
    'role_permissions',
    'purchases',
    'medicine_batches',
    'devices',
    'audit_events',
    'tenant_subscriptions',
    'suppliers',
    'shops',
    'roles',
    'medicines',
    'customers',
    'auth_sessions',
    'users',
    'tenants',
    'subscription_plans',
    'permissions',
]


def upgrade() -> None:
    bind = op.get_bind()
    for statement in CREATE_TABLES:
        op.execute(sa.text(statement))
    for statement in CREATE_INDEXES:
        op.execute(sa.text(statement))

    permission_table = sa.table("permissions", sa.column("code", sa.String), sa.column("description", sa.String))
    for code, description in PERMISSIONS:
        bind.execute(pg_insert(permission_table).values(code=code, description=description).on_conflict_do_nothing(index_elements=["code"]))
    plan_table = sa.table("subscription_plans", sa.column("id", sa.Uuid), sa.column("code", sa.String), sa.column("name", sa.String), sa.column("max_shops", sa.Integer), sa.column("max_devices_per_shop", sa.Integer), sa.column("features", sa.JSON), sa.column("is_active", sa.Boolean))
    bind.execute(pg_insert(plan_table).values(id=uuid.UUID(TRIAL_PLAN_ID), code="trial", name="Trial", max_shops=3, max_devices_per_shop=5, features={"batch_inventory": True, "api": True}, is_active=True).on_conflict_do_nothing(index_elements=["code"]))

    for table_name in TENANT_TABLES:
        scope_column = "id" if table_name == "tenants" else "tenant_id"
        policy_name = f"tenant_isolation_{table_name}"
        op.execute(sa.text(f'ALTER TABLE "{table_name}" ENABLE ROW LEVEL SECURITY'))
        op.execute(sa.text(f'ALTER TABLE "{table_name}" FORCE ROW LEVEL SECURITY'))
        op.execute(sa.text(
            f'CREATE POLICY "{policy_name}" ON "{table_name}" '
            f'USING (current_setting(\'app.tenant_id\', true) IS NOT NULL AND "{scope_column}"::text = current_setting(\'app.tenant_id\', true)) '
            f'WITH CHECK (current_setting(\'app.tenant_id\', true) IS NOT NULL AND "{scope_column}"::text = current_setting(\'app.tenant_id\', true))'
        ))


def downgrade() -> None:
    for table_name in DROP_TABLES:
        op.execute(sa.text(f'DROP TABLE IF EXISTS "{table_name}" CASCADE'))
