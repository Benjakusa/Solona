-- SOLONA Pharmacy local database schema
-- SQLite. Foreign keys enforced by the connection (PRAGMA foreign_keys = ON).

CREATE TABLE IF NOT EXISTS users (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    username        TEXT NOT NULL UNIQUE,
    password_hash   TEXT NOT NULL,
    full_name       TEXT,
    role            TEXT NOT NULL CHECK (role IN ('admin', 'cashier')),
    is_active       INTEGER NOT NULL DEFAULT 1,
    created_at      TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS suppliers (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL,
    contact         TEXT,
    address         TEXT,
    payable_balance REAL NOT NULL DEFAULT 0,   -- amount the shop currently owes this supplier
    created_at      TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS customers (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL,
    phone           TEXT,
    credit_balance  REAL NOT NULL DEFAULT 0,   -- amount this customer currently owes (udhaar)
    created_at      TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS medicines (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    name                TEXT NOT NULL,
    generic_name        TEXT,
    manufacturer        TEXT,              -- company/brand owner, e.g. "GSK" (distinct from generic_name/supplier)
    category            TEXT,
    barcode             TEXT,
    batch_no            TEXT,
    expiry_date         TEXT,              -- ISO format YYYY-MM-DD
    quantity            INTEGER NOT NULL DEFAULT 0,  -- always in the smallest sold unit (e.g. tablets)
    units_per_pack      INTEGER NOT NULL DEFAULT 1,  -- e.g. 10 tablets/strip -- informational + a Billing quick-add convenience, not a second unit of account
    purchase_price      REAL NOT NULL DEFAULT 0,
    sale_price          REAL NOT NULL DEFAULT 0,
    supplier_id         INTEGER,
    low_stock_threshold INTEGER NOT NULL DEFAULT 10,
    is_controlled_substance INTEGER NOT NULL DEFAULT 0,  -- narcotic/psychotropic (DRAP SRO 808(I)/2001)
    is_active           INTEGER NOT NULL DEFAULT 1,
    created_at          TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    updated_at          TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    FOREIGN KEY (supplier_id) REFERENCES suppliers(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS sales (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    invoice_no      TEXT NOT NULL UNIQUE,
    customer_id     INTEGER,
    cashier_id      INTEGER NOT NULL,
    date            TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    total_amount    REAL NOT NULL DEFAULT 0,
    discount        REAL NOT NULL DEFAULT 0,
    tax             REAL NOT NULL DEFAULT 0,
    payment_method  TEXT NOT NULL DEFAULT 'cash',  -- cash/card/easypaisa/jazzcash/bank/udhaar
    amount_paid     REAL NOT NULL DEFAULT 0,       -- may be less than total_amount (rest -> customer credit)
    doctor_name     TEXT,                           -- optional prescription reference
    is_refunded     INTEGER NOT NULL DEFAULT 0,     -- fully refunded/voided sale
    FOREIGN KEY (customer_id) REFERENCES customers(id) ON DELETE SET NULL,
    FOREIGN KEY (cashier_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS sale_items (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    sale_id         INTEGER NOT NULL,
    medicine_id     INTEGER NOT NULL,
    quantity        INTEGER NOT NULL,
    unit_price      REAL NOT NULL,
    subtotal        REAL NOT NULL,
    returned_qty    INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (sale_id) REFERENCES sales(id) ON DELETE CASCADE,
    FOREIGN KEY (medicine_id) REFERENCES medicines(id)
);

-- Udhaar (credit) payments a customer makes over time against their balance.
CREATE TABLE IF NOT EXISTS customer_payments (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id     INTEGER NOT NULL,
    amount          REAL NOT NULL,
    payment_method  TEXT NOT NULL DEFAULT 'cash',
    note            TEXT,
    recorded_by     INTEGER,
    date            TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    FOREIGN KEY (customer_id) REFERENCES customers(id) ON DELETE CASCADE,
    FOREIGN KEY (recorded_by) REFERENCES users(id)
);

-- Payments the shop makes to a supplier against what it owes them (accounts
-- payable -- the mirror image of customer udhaar). Deliberately NOT
-- ON DELETE CASCADE: suppliers.delete_supplier() blocks deleting a supplier
-- that has any payment history instead, so that history is never silently
-- destroyed (see the same fix applied to customer deletion).
CREATE TABLE IF NOT EXISTS supplier_payments (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    supplier_id     INTEGER NOT NULL,
    amount          REAL NOT NULL,
    payment_method  TEXT NOT NULL DEFAULT 'cash',
    note            TEXT,
    recorded_by     INTEGER,
    date            TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    FOREIGN KEY (supplier_id) REFERENCES suppliers(id),
    FOREIGN KEY (recorded_by) REFERENCES users(id)
);

-- Returns/refunds against a specific sale line item (restocks inventory).
CREATE TABLE IF NOT EXISTS sale_returns (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    sale_id         INTEGER NOT NULL,
    sale_item_id    INTEGER NOT NULL,
    medicine_id     INTEGER NOT NULL,
    quantity        INTEGER NOT NULL,
    refund_amount   REAL NOT NULL,
    reason          TEXT,
    processed_by    INTEGER,
    date            TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    FOREIGN KEY (sale_id) REFERENCES sales(id) ON DELETE CASCADE,
    FOREIGN KEY (sale_item_id) REFERENCES sale_items(id),
    FOREIGN KEY (medicine_id) REFERENCES medicines(id),
    FOREIGN KEY (processed_by) REFERENCES users(id)
);

-- Accountability trail: who did what, when. Covers deletions, discounts,
-- restores, and user-management changes across a multi-cashier shop.
CREATE TABLE IF NOT EXISTS audit_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         INTEGER,
    username        TEXT,
    action          TEXT NOT NULL,
    details         TEXT,
    timestamp       TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS backup_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    filename        TEXT NOT NULL,
    date            TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    size            INTEGER NOT NULL,
    type            TEXT NOT NULL CHECK (type IN ('manual', 'auto'))
);

-- Simple key/value store for configurable settings (low-stock threshold default,
-- expiry alert window, last auto-backup date, etc.)
CREATE TABLE IF NOT EXISTS settings (
    key             TEXT PRIMARY KEY,
    value           TEXT
);

CREATE INDEX IF NOT EXISTS idx_medicines_name ON medicines(name);
CREATE INDEX IF NOT EXISTS idx_medicines_category ON medicines(category);
CREATE INDEX IF NOT EXISTS idx_medicines_expiry ON medicines(expiry_date);
CREATE INDEX IF NOT EXISTS idx_sales_date ON sales(date);
CREATE INDEX IF NOT EXISTS idx_sale_items_sale ON sale_items(sale_id);
CREATE INDEX IF NOT EXISTS idx_customer_payments_customer ON customer_payments(customer_id);
CREATE INDEX IF NOT EXISTS idx_supplier_payments_supplier ON supplier_payments(supplier_id);
CREATE INDEX IF NOT EXISTS idx_sale_returns_sale ON sale_returns(sale_id);
CREATE INDEX IF NOT EXISTS idx_audit_log_timestamp ON audit_log(timestamp);
