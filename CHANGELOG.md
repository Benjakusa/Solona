# Changelog

## Unreleased — SOLONA brand update

- Rebranded the application interface, defaults, installer, documentation, and Windows executable to SOLONA Pharmacy. Existing database paths, keyring namespaces, environment-variable aliases, and LAN protocol headers remain supported for upgrades.

## Unreleased — Stage 5 release audit

- Removed the universal local admin recovery PIN and fixed reset password. Recovery now requires a shop-configured PIN and a caller-chosen 12+ character password.
- Replaced the shared first-run administrator password with a unique random per-database credential; packaged first run stores it temporarily in Windows Credential Manager and displays it once.
- Changed desktop SQLite backup/restore to use the SQLite online backup API and integrity validation.
- Added repeatable Windows build script, executable version metadata, deployment and recovery runbooks, and release-readiness evidence.
- Release classification: internal testing build. Clean Windows packaging, full PostgreSQL/end-to-end matrix, Safaricom sandbox, restore drills, security scanning, and Kenyan compliance evidence remain incomplete.

## Unreleased — Stage 2 backend foundation

- Added a separate versioned FastAPI service, PostgreSQL SQLAlchemy models, and Alembic bootstrap migration with forced row-level tenant policies.
- Added tenant registration, password authentication, short-lived access tokens, rotating refresh sessions, invitation acceptance, tenant/shop/member/device management, pharmacy receipt and sales APIs, and audit events.
- Added batch-level stock receipt/deduction with transactional movement records and server-derived prices.
- Added PostgreSQL integration tests and backend configuration/deployment documentation. Tests require a disposable PostgreSQL database and remain unverified in environments without one.
- Desktop UI and local SQLite schema are unchanged by this stage. Offline synchronization and importing legacy SQLite data remained pending at Stage 2.

## Unreleased — Stage 1 audit

- Added current-state architecture and proposed modular offline-first SaaS architecture documentation.
- Documented current SQLite entities, target Platform → Tenant → Shop → Device ownership, identity migration, and reconciliation strategy.
- Recorded security findings, limitations, runtime/test baseline, and implementation status.
- No application code, schema, dependency, or pharmacy workflow was changed.
