# SOLONA Pharmacy

SOLONA Pharmacy is a PySide6 desktop application for pharmacy sales and operations. It uses a local SQLite database for day-to-day work and includes an optional FastAPI/PostgreSQL service foundation for multi-tenant cloud workflows.

> **Release status: Internal testing only.** The repository is not ready for production pharmacy operations. Windows deployment, recovery, security, payment-provider, sync-conflict, operational, and Kenyan regulatory checks remain. Read [DEPLOYMENT.md](DEPLOYMENT.md), [BACKUP_AND_RECOVERY.md](BACKUP_AND_RECOVERY.md), and [IMPLEMENTATION_STATUS.md](IMPLEMENTATION_STATUS.md) before evaluating a release.

## Desktop features

- Medicine catalogue and stock management, including batch and expiry information, low-stock alerts, barcode lookup, and label generation.
- Point-of-sale billing with cart, invoices, payment methods, customer credit (udhaar), and partial payments.
- Customer and supplier records, customer payment history, purchase intake, and supplier payables.
- Partial and full returns, sales and operations reports, PDF and Excel exports, and receipt printing.
- Admin and cashier roles, audit history, configurable alerts, and backup/restore.
- Local offline sales with a durable sync outbox. Cloud login and an optional shop LAN coordinator are available with provisioning and recovery constraints; see [Sync and networking](#sync-and-networking).

## Screenshots

| Login | Dashboard |
|---|---|
| ![Login screen](screenshots/login.png) | ![Dashboard](screenshots/dashboard.png) |

| Inventory | Billing |
|---|---|
| ![Inventory](screenshots/inventory.png) | ![Billing and point of sale](screenshots/billing.png) |

| Customers | Suppliers |
|---|---|
| ![Customers](screenshots/customers.png) | ![Suppliers](screenshots/suppliers.png) |

| Reports | Backup and restore |
|---|---|
| ![Reports](screenshots/reports.png) | ![Backup and restore](screenshots/backup_restore.png) |

## Requirements

- Python 3.11 or newer is suitable for the desktop source; the packaged Windows build is intended for Windows 10/11 x64.
- Install the desktop packages from `requirements.txt`.
- A printer is optional. Receipt printing depends on the operating system and configured printer.
- The desktop's local features do not require internet access. Cloud sync and online sign-in require a separately deployed backend and network access.

The Windows executable and installer have not been verified on a clean Windows machine. Linux is useful for source work, but the Windows executable must be built on Windows; see [DEPLOYMENT.md](DEPLOYMENT.md).

## Run the desktop app from source

```bash
python -m venv .venv

# Windows PowerShell
.venv\Scripts\Activate.ps1

# Linux or macOS: use `source .venv/bin/activate` instead
python -m pip install -r requirements.txt
python main.py
```

On first launch, SOLONA creates its local database and a unique administrator password. In a development run, the temporary password is printed once in the terminal. Sign in with username `admin`, then change the password. In the packaged Windows app, the temporary password is provisioned through Windows Credential Manager and displayed once. Configure an owner Recovery PIN in Settings and store it securely.

By default, source runs store the database under `data/pharmacy.db` in the project directory. Set `SOLONA_DB_PATH` to choose a different SQLite file. A packaged Windows installation stores data under `%LOCALAPPDATA%\PharmacyManagementSystem`, outside the application install directory.

## Roles and modules

**Admins** can manage users, medicines, suppliers, settings, backups, and all other modules. **Cashiers** can use billing and read-only inventory, customers, and reports. User-interface visibility is a convenience boundary; see [SECURITY_MODEL.md](SECURITY_MODEL.md) for the application's security limitations.

The desktop includes Dashboard, Billing, Returns, Inventory, Customers, Suppliers, Reports, Backup / Restore, and Settings. Cashiers see a restricted subset.

## Backups and data recovery

Use the in-app Backup / Restore page to create a database backup and review backup history. Automatic backups run during normal app lifecycle events. Before an upgrade or recovery operation, make a backup and follow [BACKUP_AND_RECOVERY.md](BACKUP_AND_RECOVERY.md). Do not copy one shop's database over another shop's data.

In development, backups are stored in the project `backups/` directory. Packaged Windows data and backups live under `%LOCALAPPDATA%\PharmacyManagementSystem`.

## Sync and networking

Cloud sync and LAN coordination are optional foundations; neither should be treated as a guarantee of consistent shared stock during network outages.

- Deploy the API using [backend/README.md](backend/README.md), then use **Sign in online / refresh offline access** in the desktop login screen. Production API connections must use HTTPS.
- Cached cloud authorization permits offline sign-in for up to 72 hours after a successful online login.
- A LAN coordinator must be initialized from a verified shop database. All terminals must use the same verified catalogue and batch baseline. Independent legacy databases cannot simply be connected as coordinated terminals.
- While disconnected from a coordinator, terminals can record local sales, but stock may diverge. Reconnection can raise conflicts for authorized review. Recording a reconciliation note does not post a stock correction automatically.
- Returns work locally, but cloud replay of returns remains a reconciliation task. Preserve terminal databases and outboxes during coordinator recovery.

Detailed coordinator setup, outage handling, and limitations are documented in [ARCHITECTURE.md](ARCHITECTURE.md), [BACKUP_AND_RECOVERY.md](BACKUP_AND_RECOVERY.md), and [backend/OPERATIONS.md](backend/OPERATIONS.md).

## Backend service

The `backend/` directory contains a separate FastAPI service using PostgreSQL. It provides tenant, shop, staff, device, pharmacy-data, synchronization, subscription, and Daraja integration foundations. It does not replace the desktop's SQLite database or automatically import existing desktop data.

Follow [backend/README.md](backend/README.md) for local setup, environment configuration, migrations, API usage, and integration tests. Use a disposable PostgreSQL database for tests. Production deployment also requires the operational controls in [backend/OPERATIONS.md](backend/OPERATIONS.md); the service foundation is not by itself a production deployment.

## Build a Windows executable and installer

Build on a clean Windows x64 environment. Install the requirements, then run:

```powershell
pyinstaller --clean pharmacy_app.spec
```

The executable is written to `dist\SOLONA.exe`. To build the installer, install Inno Setup and run:

```powershell
.\build_windows.ps1
```

The installer output and signing/release requirements are described in [DEPLOYMENT.md](DEPLOYMENT.md). Test installation, upgrade, printing, keyring behavior, and backup/restore on a clean Windows VM before distributing a build.

## Project layout

```text
main.py                 Desktop entry point
ui/                     PySide6 windows and views
logic/                  Desktop business logic and integrations
database/               SQLite schema, initialization, and migrations
network/                Optional shop LAN coordinator
backend/                Separate FastAPI/PostgreSQL service
screenshots/             UI screenshots
```

See [ARCHITECTURE.md](ARCHITECTURE.md) for current component boundaries, [DATA_MODEL.md](DATA_MODEL.md) for the data model, and [TESTING.md](TESTING.md) for recorded verification evidence and limitations.
