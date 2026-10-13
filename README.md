# SOLONA Pharmacy

SOLONA is an offline-capable pharmacy point-of-sale and operations application. The desktop client stores shop data in SQLite. A separate FastAPI/PostgreSQL service and a shop LAN coordinator provide optional synchronization foundations; they are separate deployments and do not automatically import or reconcile an existing shop database.

> **Release status: internal evaluation only.** The hardening work is in progress. Do not use this repository as evidence that a pharmacy deployment is production-ready. See [Production readiness](PRODUCTION_READINESS.md), [Audit results](AUDIT_RESULTS.md), and [Decisions](DECISIONS.md) for verified changes and remaining release gates.

## What the desktop app does

- Catalogue, batch stock, expiry tracking, barcode lookup, and stock alerts
- Checkout, invoices, payment methods, customer credit, and partial payments
- Returns, supplier purchases, customer and supplier records
- Sales and operations reports, exports, receipts, and audit history
- User administration, local backup and restore, and optional cloud/LAN sync clients

Desktop permissions and business rules are still being hardened. Read [SECURITY_MODEL.md](SECURITY_MODEL.md) before relying on role controls.

## Run from source

Requirements: Python 3.11 or newer. Printing is optional and depends on the host operating system and printer setup.

```bash
python -m venv .venv

# Windows PowerShell
.venv\Scripts\Activate.ps1

# Linux or macOS
source .venv/bin/activate

python -m pip install -r requirements.txt
python main.py
```

The first launch initializes a local database and provisions a unique administrator password. For source runs, the temporary credential is printed once in the terminal. Sign in as `admin`, change that password, then configure and securely store a Recovery PIN in Settings.

By default, the source database is `data/pharmacy.db`. Set `SOLONA_DB_PATH` to use another path. Packaged Windows builds use `%LOCALAPPDATA%\\PharmacyManagementSystem` for application data.

## Optional backend

The backend is a separate FastAPI service backed by PostgreSQL. It contains tenant, shop, staff, device, pharmacy-data, synchronization, subscription, and payment integration foundations. Follow [backend/README.md](backend/README.md) for setup and [backend/OPERATIONS.md](backend/OPERATIONS.md) for deployment considerations. Integration tests require a disposable PostgreSQL database configured with `TEST_DATABASE_URL`.

## Optional shop LAN coordinator

The coordinator owns its SQLite database; terminals communicate with it over HTTP and must not open its database file directly. It requires explicit enrollment and a shop-specific enrollment secret. Review [ARCHITECTURE.md](ARCHITECTURE.md), [BACKUP_AND_RECOVERY.md](BACKUP_AND_RECOVERY.md), and [Production readiness](PRODUCTION_READINESS.md) before connecting terminals. Network security, reconciliation, and operational release gates remain open.

## Backups and recovery

Use the in-app Backup / Restore screen and follow [BACKUP_AND_RECOVERY.md](BACKUP_AND_RECOVERY.md). Keep backups outside the application's install directory and verify recovery procedures with a copy of shop data before upgrades. Never restore one shop's database over another shop's live data.

## Build for Windows

Build on a clean Windows x64 machine. Install the project requirements, then run:

```powershell
pyinstaller --clean pharmacy_app.spec
```

For the installer, install Inno Setup and run `build_windows.ps1`. Packaging, signing, installation, upgrade, printing, and recovery still need clean Windows validation; see [DEPLOYMENT.md](DEPLOYMENT.md).

## Repository map

```text
main.py       Desktop entry point
ui/           PySide6 screens
logic/        Desktop business logic
database/    SQLite schema and initialization
network/      Optional shop LAN coordinator
backend/      FastAPI and PostgreSQL service
```

See [ARCHITECTURE.md](ARCHITECTURE.md), [DATA_MODEL.md](DATA_MODEL.md), [TESTING.md](TESTING.md), and [IMPLEMENTATION_STATUS.md](IMPLEMENTATION_STATUS.md) for more detail.
