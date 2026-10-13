# Testing and baseline record

## Stage 1 baseline

Recorded on 2026-10-12 from the cloned repository at `stage-1-audit`, starting commit `d192745` (`Fix silent print failures and a thermal-receipt filename collision`). The worktree was clean before documentation changes.

| Check | Result |
| --- | --- |
| Python interpreter | Python 3.13.7 |
| Declared dependencies | PySide6 6.11.2; reportlab 4.2.2; openpyxl 3.1.5; python-barcode 0.16.1; Pillow 12.3.0; pyinstaller 6.22.0 |
| Installed runtime dependencies | None of the declared third-party packages were installed in the audit interpreter. |
| Automated tests | No test files or pytest/tox/pyproject test configuration found. `pytest` is not installed. |
| Static syntax compilation | `python -m compileall -q main.py database logic ui` completed successfully. Syntax only, not runtime behavior. |
| Application startup | `python main.py` failed before UI startup: `ModuleNotFoundError: No module named 'PySide6'`. |
| Main workflows | Not runtime verified. No claim is made that login, POS, inventory, reports, printing, or restore work in this environment. |
| Local DB backup | No `data/`, `backups/`, or `data/pharmacy.db` existed in this clone; there was no customer database to back up. |
| Git checkpoint | `stage-1-audit` created from clean `main` at `d192745`, preserving the pre-documentation source baseline. |

The app is Windows-oriented: `main.py` uses `ctypes.windll` for a Windows named mutex, so Linux execution would need a platform check even after dependencies are installed. That path was not reached because PySide6 is missing. Dependencies were not installed during this audit.

## Existing coverage and gaps

There are no automated tests. Add isolated SQLite tests for schema creation/upgrades and feature-service tests before changing behavior. Priority coverage:

1. Login, inactive users, recovery, password policy, and authorization denial.
2. Sale atomicity, stock races, invoice uniqueness, customer credit, refunds/returns, and exact totals.
3. Supplier receipts, batch/expiry behavior, payment balances, and inventory adjustments.
4. Migration from representative old database versions, interrupted upgrade, backup integrity and restore, including recovery after failed restore.
5. Roles at service/API boundary, tenant/shop isolation, device revocation, duplicate/out-of-order sync events, and conflict resolution.
6. Receipt generation, printer-unavailable behavior, export/report totals, and offline workflow recovery.

## Test strategy for the conversion

- Keep deterministic fixtures with synthetic pharmacy data; never use live customer records in test logs or shared artifacts.
- Use a temporary SQLite database per test and explicit schema versions.
- Unit test pure domain calculations without Qt; integration test repository transactions against SQLite; add UI smoke tests when useful.
- For cloud modules, test authentication and scope on every endpoint, use PostgreSQL integration tests for constraints/RLS, and attempt cross-tenant reads/writes deliberately.
- Test sync as at-least-once delivery: duplicates, retries, missing acknowledgments, device revocation, clock skew, prolonged outage, and conflicting stock/sale events.
- Exercise migration and rollback/restore against copies of synthetic legacy databases, comparing row counts, money totals, balances, and quantities.
- Test Windows packaging and printing separately on supported Windows versions; Linux syntax compilation does not verify installer/runtime behavior.

## Stage 1 acceptance status

Source architecture, schema, limitations, security risks, target model, baseline constraints, and migration approach are documented. Runtime workflow verification and database backup were unavailable because dependencies are missing and no database file was present. Stage 1 is not represented as having a successful runtime baseline.

## Stage 2 backend integration verification

Tests in `backend/tests/` require a disposable PostgreSQL database whose name ends in `_test` or `_tests`. They exercise fresh Alembic upgrade/downgrade, tenant and shop isolation, role and shop assignments, duplicate/invalid references, device revocation, batch-level stock deduction, concurrent sale attempts, transaction rollback, refresh-token reuse, and subscription enforcement. Set `TEST_DATABASE_URL` to that disposable database. Never point it at a database containing user data: fixtures drop the schema during migration recovery checks.

Final run on 2026-10-12: **10 passed, 1 warning in 11.42 seconds** against PostgreSQL 14.20 with a disposable `pharmacy_backend_test` database and a non-superuser role without `BYPASSRLS`. The warning is from Starlette deprecating its current `httpx` TestClient integration. `python -m compileall -q backend` and `git diff --check` also completed successfully.

These integration tests are not a penetration test, production backup drill, provider integration test, or desktop workflow verification.

## Stage 3 offline/sync verification

The desktop logic tests run with `python -m pytest -q tests` from the repository root. They use isolated temporary SQLite databases and cover atomic sale/outbox writes, rollback on outbox failure, retry timing/acknowledgment, 72-hour cached authorization, catalogue conflict detection, concurrent local sales, FEFO batch deduction, purchase/return movements, legacy opening-batch migration, cloud bootstrap/replay idempotency, LAN serialization/stale stock, and device signature/revocation.

Backend API tests run from `backend/` against a disposable PostgreSQL 14+ database named with the `_test` or `_tests` suffix:

```bash
TEST_DATABASE_URL='postgresql+psycopg://USER@127.0.0.1:5432/pharmacy_backend_test' \
APP_ENV=test APP_SECRET_KEY='replace-with-a-local-test-secret' \
python -m pytest -q tests
```

The fixture drops/recreates its schema and must never target business data. Stage 3 final run on 2026-10-12: desktop foundation **13 passed**; backend PostgreSQL integration **12 passed, 1 warning**. Backend tests cover device-scoped bootstrap/feed, event replay idempotency, stale-stock conflict, and revoked devices. The UI was syntax-checked but not launched because PySide6 is not installed in the current Linux environment. LAN behavior is tested directly at the coordinator service boundary, not with a multi-host network fault-injection harness. Receipt printer behavior and full multi-terminal recovery are still manual verification items.

## Stage 5 release audit (2026-10-12)

- `python -m pytest -q tests`: **not run**; pytest is not installed in the active Python 3.13 environment.
- `python -m pytest -q backend/tests`: **not run**; test setup failed importing missing `pydantic`. No disposable PostgreSQL database was supplied.
- `python -m compileall -q main.py database logic ui backend/app backend/tests`: **passed** (syntax only).
- `git diff --check`: **passed**.
- Windows PyInstaller/Inno Setup build and clean-VM verification: **not run**; the current host is Linux.
- Daraja sandbox end-to-end, adversarial cross-tenant scan, restore drill, dependency vulnerability scan, full UI/printing matrix, prolonged outage/crash harness, and Kenyan compliance verification: **not run**.

At the time of the Stage 5 audit, pytest was unavailable in the audit interpreter; the audit fixed the universal local recovery PIN/fixed reset password and shared first-run password in source. First run now creates a random password, and recovery requires the configured shop PIN plus a caller-chosen password (12+ characters). SQLite backup/restore uses SQLite's online backup API and validates staged files. The test results in the following P0/P1 sections are later runs on the current worktree. Prior Stage 2/3 counts above are historical. The current release classification is **INTERNAL TESTING ONLY**.

## P0 remediation and closure review (2026-10-12)

An isolated P0 verification run after the Daraja and offline-sale replay fixes recorded:

| Scope | Command / environment | Result |
| --- | --- | --- |
| Daraja callbacks and sale replay | From `backend/`: `pytest -q tests/test_api_integration.py -k "daraja_callback or replay"`, disposable PostgreSQL | **3 passed, 12 deselected** |
| Backend integration | From `backend/`: `pytest -q tests`, disposable PostgreSQL and non-superuser test role | **15 passed**, including migration downgrade/recovery and tenant/RLS tests; 1 Starlette/httpx deprecation warning |
| Desktop offline/sync | Repository root: `pytest -q tests/test_offline_foundation.py tests/test_shop_coordinator.py` | **15 passed** |
| Static checks | `python -m compileall -q main.py database logic ui backend/app backend/tests`; `git diff --check` | **passed** |

The P0 closure review found no remaining implementation blocker. Callback data cannot confirm payment; subscription activation requires a correlated HTTPS Daraja STK Push Query result. Replay uses one authoritative implementation and applies validated sale, credit, stock, movement, audit and inbox changes in the request transaction. Duplicate delivery is idempotent and changed content under a reused event identifier conflicts.

The recorded backend test database was disposable. No live or sandbox Safaricom request was made. The independent closure reviewer did not rerun tests. Revision `0005_sale_replay_amounts` upgrades additively; pre-existing sales receive zero discount/tax defaults, while pre-existing Daraja attempts have NULL environment/account and cannot be automatically reconciled. Downgrading after new values are stored drops the added metadata and is not lossless; take a backup before any downgrade. A populated pre-0005 upgrade-preservation drill has not been retained as separate evidence.

P0 closure review: **PASS with these limitations recorded**. Release classification remains **INTERNAL TESTING ONLY**. Sandbox payment validation, complete tenant-isolation verification, synchronization conflict/recovery behavior, Windows deployment, backup recovery, regulatory review and the remaining operational checks are still release gates.

## P1 bounded change: cloud sale-return replay (2026-10-12)

Added an additive PostgreSQL migration for returned quantities and tenant-scoped return records. The sync endpoint now accepts `sale.returned` only from an authorized sales writer, correlates the original cloud sale, stable sale-line ID and batch, checks refund and credit calculations against the original ledger, and applies stock, sale, customer, return, movement, audit, and inbox changes in one transaction. Desktop return outbox events now include the stable cloud correlation identifiers. Events without those identifiers remain conflicts for review.

| Scope | Environment | Result |
| --- | --- | --- |
| New cloud return regression | PostgreSQL 14.20 in fresh `pg_virtualenv`, disposable `pharmacy_backend_test` | **1 passed** |
| Full backend integration suite | PostgreSQL 14.20 in fresh `pg_virtualenv`, disposable `pharmacy_backend_test`, non-superuser role | **16 passed, 1 deprecation warning** |
| Desktop offline and coordinator tests | Temporary SQLite data | **15 passed** |
| Compile and diff checks | Python 3.13.7 | **passed** |

The backend test fixture downgraded and recreated only the disposable database. No business database or payment provider was contacted. Migration `0006_sale_return_replay` adds `returned_qty` with a zero default for existing sale lines; downgrade drops the return ledger and returned quantities, so take a backup before downgrading after returns have been recorded. This bounded P1 change does not verify offline conflict recovery across long outages, Windows packaging, actual Safaricom sandbox behavior, or production backups. Release classification remains **INTERNAL TESTING ONLY**.
