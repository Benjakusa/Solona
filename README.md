# SOLONA Pharmacy

## Stage 3 offline sync and shop coordinator

The desktop keeps its local SQLite database and can record supported sales while offline. Cloud login links a terminal to a selected tenant/shop, registers its device, and stores the device credential in the operating system keyring (`keyring` is included in `requirements.txt`). Cached cloud-user sign-in expires after 72 hours without a successful online login. The status bar shows the current workspace/device, pending events, last successful sync, errors, and unresolved conflicts. A standalone terminal clearly warns that stock may diverge from other terminals.

For cloud sync, deploy the service described in [backend/README.md](backend/README.md), use an HTTPS API URL, install desktop requirements, then use **Online sign in** on the login screen. The first sign-in requires tenant/shop permission and device enrollment. Existing local-only records are assigned to the explicitly selected workspace after confirmation; unmatched legacy staff/catalog records may need reconciliation before cloud replay.

For an optional shop coordinator, prepare one verified shop database and seed the coordinator from it:

```bash
export SOLONA_LAN_DB=/var/lib/solona/shop-coordinator.db
export SOLONA_LAN_ENROLLMENT_SECRET='store-a-long-random-secret-in-the-service-environment'
python -m network.shop_server --database "$SOLONA_LAN_DB" --seed-from /path/to/verified/pharmacy.db --host 0.0.0.0 --port 8765
```

Protect the coordinator host and database. Use a private trusted LAN; put a TLS reverse proxy in front of it if requests cross an untrusted network. Enroll terminals with `python -m logic.sync configure-lan --url http://SHOP-SERVER:8765`; the command prompts for the enrollment secret and stores its device credential in the OS keyring. Set the same stable enrollment secret in the coordinator service environment. Revoke a terminal with an authenticated `DELETE /devices/{device_uuid}` request carrying the legacy `X-Shani-Enrollment` protocol header; this header remains supported for existing enrolled terminals.

Provision every terminal from the same verified catalogue/batch baseline before connecting it to the LAN coordinator. The coordinator currently seeds batch balances and event history but does not produce a full client database snapshot. Do not connect independent legacy databases with different batch IDs as coordinated terminals. If the coordinator fails, preserve each terminal's SQLite file and outbox. Restore the latest verified coordinator backup; if unavailable, rebuild from the authoritative baseline and reconcile all terminal events/conflicts before calling inventory coordinated again. Terminal sales made during server loss remain local and can result in visible stock conflicts after recovery.

Returns work locally, but cloud replay of returns is not yet supported; they remain for authorized reconciliation. Resolving a conflict stores a required note but does not automatically post a stock correction.

## Stage 2 backend foundation

The `backend/` directory contains a separately deployable FastAPI and PostgreSQL service foundation. For setup, environment configuration, Daraja subscription billing, operations gates, migrations, security boundaries, and PostgreSQL integration tests, see [backend/README.md](backend/README.md) and [backend/OPERATIONS.md](backend/OPERATIONS.md). Device-authenticated sync endpoints are included; automated legacy-data migration and a graphical SaaS administration console remain future work.

This repository is currently classified as **INTERNAL TESTING ONLY**. See [DEPLOYMENT.md](DEPLOYMENT.md), [BACKUP_AND_RECOVERY.md](BACKUP_AND_RECOVERY.md), [TESTING.md](TESTING.md), and [IMPLEMENTATION_STATUS.md](IMPLEMENTATION_STATUS.md) for release evidence and blockers. Do not use it for production pharmacy operations until the documented security, Windows, payment, recovery, and Kenyan compliance gates are verified.

A fully offline desktop application for managing a medical pharmacy: inventory,
billing/POS, customers, suppliers, users, reports, and backup/restore.

## Tech stack

- **Python 3** + **PySide6** (Qt6 GUI bindings)
- **SQLite** (local file, auto-created on first run — see "Where data lives" below)
- **reportlab** for PDF invoices/reports, **openpyxl** for Excel exports
- **PyInstaller** for packaging into a single Windows `.exe`

The intended packaged client is Windows 10/11 x64; these versions and printer models have not yet been certified on clean machines. The backend declares Python 3.11+ and PostgreSQL 14+. Current release classification is **INTERNAL TESTING ONLY**, not production-ready.

No internet connection is required for any feature.

## Screenshots

| Login | Dashboard |
|---|---|
| ![Login screen](screenshots/login.png) | ![Dashboard with low-stock and expiry alerts](screenshots/dashboard.png) |

| Inventory | Billing / POS |
|---|---|
| ![Inventory management with barcode column and expiry/low-stock highlighting](screenshots/inventory.png) | ![Billing screen with a cart and totals](screenshots/billing.png) |

| Customers | Suppliers |
|---|---|
| ![Customer management](screenshots/customers.png) | ![Supplier management with purchase orders](screenshots/suppliers.png) |

| Reports | Backup & Restore |
|---|---|
| ![Daily sales report](screenshots/reports.png) | ![Backup and restore with history](screenshots/backup_restore.png) |

| Settings | Cashier role (restricted view) |
|---|---|
| ![Settings and user management](screenshots/settings.png) | ![Cashier sees only billing and read-only inventory/customers/reports](screenshots/cashier_view_inventory_readonly.png) |

## Project structure

```
pharmacy-app/
  main.py                 Entry point
  requirements.txt
  pharmacy_app.spec       PyInstaller build spec
  installer.iss           Inno Setup script (builds the deployable installer)
  database/
    schema.sql            Table definitions
    db_manager.py          Connection handling, schema init, unique first-run admin credential
  logic/                   Business logic (one module per feature area)
    security.py            Password hashing (PBKDF2)
    auth.py                 Login + user management
    inventory.py            Medicine CRUD, stock, low-stock/expiry alerts
    suppliers.py             Supplier CRUD + purchase orders (stock intake)
    customers.py             Customer CRUD + sales history
    sales.py                  Cart + checkout (POS)
    invoice_pdf.py             Printable invoice PDF generation
    reports.py                  Daily/monthly/profit-loss/expiry reports
    report_export.py             Excel/PDF export helpers
    backup.py                     Backup/restore/auto-backup/retention
    settings.py                    Configurable thresholds
  ui/                      PyQt/PySide6 forms (one view per feature area)
  data/                    pharmacy.db lives here (auto-created)
  backups/                 Backup files land here by default
  invoices/                Generated invoice PDFs
```

## Running the app (development)

```bash
python -m venv venv
venv\Scripts\activate          # Windows
pip install -r requirements.txt
python main.py
```

On first run, the database and tables are created automatically with a local
`admin` account. Set and record a unique strong password before entering
business data. The application does not use a shared documented first-run
password. Configure the owner Recovery PIN in Settings before relying on
offline password recovery. Recovery requires that passphrase and a new password of
at least 12 characters; no developer master PIN or fixed reset password is
included. If both credentials are lost, follow the shop's verified backup
and data recovery process; do not share database files with untrusted parties.

Only one copy of the app can run at a time (a shortcut double-clicked twice
by accident shows "Already Running" instead of opening a confusing second
window on the same data).

## Roles

- **Admin**: full access to every module, including Inventory add/edit/delete,
  Suppliers, Backup/Restore, Settings, and User Management.
- **Cashier**: Billing (POS) and read-only Inventory/Customers/Reports. No
  edit/delete rights.

## Payment methods & Udhaar (credit)

Billing supports six payment methods: Cash, Card, EasyPaisa, JazzCash, Bank
Transfer, and **Udhaar (credit)**. A sale doesn't have to be fully paid or
fully credit — the cashier enters an **Amount Paid**, and whatever is left
of the total is automatically added to that customer's udhaar balance
(walk-in customers can't be given credit; a customer must be selected
first). The Customers screen shows each customer's outstanding balance and
has a **Record Payment** button to log partial or full udhaar repayments
(cash, card, or any other method) — the balance and the customer's payment
history update immediately. A customer with an outstanding balance cannot
be deleted until it's settled. Reports → Customer Credit lists every
customer who currently owes money, most-owed first, so following up on
udhaar is a two-click task instead of manual bookkeeping.

## Returns & refunds

The Returns page finds any past sale by invoice number and lets you return
specific line items, in full or partial quantity (e.g. 2 of the 5 strips
sold). Returning an item restocks it back into Inventory automatically. If
that sale was paid partly or fully on udhaar, the refund reduces the
customer's outstanding balance first, and only the remainder (if any) is
handed back as cash — so a return can never accidentally overpay a
customer or under-correct their credit.

## Controlled substances (DRAP compliance)

Medicines that are narcotics/psychotropics can be flagged **🔒 Controlled
Substance** in Inventory → Add/Edit Medicine. Every sale of a flagged
medicine is automatically captured in Reports → Controlled Substances, a
register-style report (date, invoice, medicine, batch, quantity, customer,
prescribing doctor, cashier) matching the documentation format pharmacies
need for DRAP (SRO 808(I)/2001) inspections. The Billing screen also has an
optional **Doctor Name** field for exactly this purpose.

## Thermal printer support

Settings → General lets you choose the receipt format: full-page **A5**
(for a regular printer) or narrow **58mm** / **80mm** (for thermal POS
printers). Checkout prints straight to the shop's default printer — no
manual "open the PDF, then print" step — using Windows' native print verb.

## Audit log

Settings → Audit Log keeps a running record of sensitive actions (large
discounts, udhaar sales, medicine/customer deletions, database restores,
user management changes) with who did it and when — useful for tracing
"who changed this" months later without digging through backups.

## More reports

Beyond daily/monthly sales and profit/loss, Reports now also has: **Dead
Stock** (medicines sitting unsold for 90+ days, so they can be discounted
or returned to the supplier before they expire), **Best Sellers** (top
medicines by quantity sold in a date range), and a 7-day sales trend chart
on the Dashboard.

## Barcode scanning (Billing)

A USB/Bluetooth barcode scanner is just a keyboard emulator — it "types" the
barcode digits into whatever text field has focus and then sends an Enter
key press. The Billing screen's search box is built for exactly this: click
into it once, then scan — each scan looks up the barcode, adds one unit to
the cart (scanning the same item again just increases its quantity), and
clears the box so it's ready for the next scan. Stock is only deducted from
Inventory when the sale is actually checked out, not while items sit in the
cart.

Barcodes are optional per medicine (set in Inventory → Add/Edit Medicine →
Barcode field) — a medicine without one can still be sold by typing its name.

**Generating your own barcodes**: many medicines (loose/repackaged stock,
locally made items) don't come with a manufacturer barcode. In the
Add/Edit Medicine dialog, click **🏷️ Generate** to create a unique internal
code (`PHxxxxxxxx`), then **🖨️ Print Label** to produce a small printable
PDF (name + price + barcode) saved to the `labels/` folder — print it,
stick it on the product, and it scans in Billing exactly like any other
barcode (Code128 encodes plain text, so there's no manufacturer-specific
format requirement).

## Backup & Restore

- **Backup Now** (Dashboard or Backup/Restore page): one click, saves a
  timestamped copy of the database to `backups/` (or a custom folder you pick,
  e.g. a USB drive).
- **Restore Backup**: pick a `.db` file, confirm the warning, and the app
  restarts automatically with the restored data. A safety copy of the
  pre-restore database is kept in `data/` just in case.
- **Backup History**: view and delete past backups from inside the app.
- **Auto-backup**: runs silently once per day on first login, and again
  whenever the app is closed. Only the last 15 auto-backups are kept
  (manual backups are never auto-deleted).

## Packaging into a single .exe

```bash
pip install pyinstaller
pyinstaller pharmacy_app.spec
```

The finished executable is written to `dist/SOLONA.exe`.

**Where data lives when packaged:** the frozen .exe stores its database,
backups, and invoices under `%LOCALAPPDATA%\PharmacyManagementSystem\` to
preserve data from existing installations
(e.g. `C:\Users\<user>\AppData\Local\PharmacyManagementSystem\data\pharmacy.db`)
— **not** next to the .exe itself. This is deliberate: the installer places
the executable in `C:\Program Files\...`, and a normal (non-admin) running
process cannot write there, so app data must live in a per-user writable
folder regardless of where the .exe is installed. In development (running
via `python main.py`, not frozen), data still lives in the project's own
`data/`/`backups/`/`invoices/` folders for convenience.

Re-run `pyinstaller pharmacy_app.spec` any time after changing the source to
rebuild.

## Building the Windows installer (for deploying to shop PCs)

The raw `.exe` above can be run directly, but for deploying to many
different shop PCs, build a proper installer with **Inno Setup**
(`installer.iss`) — it adds a Start Menu entry (so the app is searchable via
Windows Search), an optional Desktop shortcut, and a proper uninstaller
listed in "Add or Remove Programs":

```bash
winget install --id JRSoftware.InnoSetup -e   # one-time, if not installed
.\build_windows.ps1
```

This produces `installer_output\SOLONA_Setup_1.0.0.exe` — copy
just this one file to each shop's PC and run it. It installs to
`C:\Program Files\PharmacyManagementSystem` (a one-time admin/UAC prompt is
normal and expected) and each shop's own data ends up in that PC's own
`%LOCALAPPDATA%\PharmacyManagementSystem\`, completely independent of other
shops' data.

Rebuild both the `.exe` and the installer together after any source change —
the installer script always bundles whatever is currently in `dist/`.

The installer itself does not contain an access password; installer passwords
are not a tenant security control. Distribute only signed, versioned builds
through an authenticated channel and verify the published SHA-256 hash. The
packaged executable has not yet been tested on a clean Windows VM; see
[DEPLOYMENT.md](DEPLOYMENT.md).

## Notes / known simplifications

- The `medicines` table (per the required schema) holds one batch/expiry per
  medicine row rather than per-batch lots; receiving new stock via a Purchase
  Order updates the batch/expiry/quantity on that same row.
- Profit/loss reporting uses each medicine's *current* purchase price (the
  schema does not store a historical cost price per sale).
