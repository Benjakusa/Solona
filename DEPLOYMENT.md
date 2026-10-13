# Deployment and release procedure

## Release classification

As of 2026-10-12 this repository is an **internal testing build**, not pilot-ready or production-ready. The backend is a deployment foundation; the desktop and backend integration matrix, packaged Windows validation, payment-provider sandbox round trip, production restore drill, and regulatory review have not all been completed. See `IMPLEMENTATION_STATUS.md` for evidence and blockers.

## Windows desktop build

The intended packaged client environment is Windows 10/11 x64. Windows versions and printer models have not been certified by this repository. Build on a clean Windows x64 runner with Python 3.13 and pinned dependencies; PyInstaller does not produce a Windows executable when run on Linux. Use a tagged source revision and build output directory named with the same version (for example `dist/1.0.0/`). Do not put API, Daraja, installer signing, or database credentials in the spec, environment files shipped with the application, or installer.

```powershell
py -3.13 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
pyinstaller --clean pharmacy_app.spec
```

The spec produces `dist\SOLONA.exe`. Compile `installer.iss` with Inno Setup after verifying the executable. The installer keeps the existing application ID and install/data directories so current installations upgrade in place. Store user data under `%LOCALAPPDATA%\PharmacyManagementSystem`, outside Program Files. First-run credentials are unique per installation and shown once; local owner Recovery PIN is not a universal support credential. Production secrets are never bundled.

Before distributing a build, run it under a standard Windows account in a clean Windows VM. Verify first-run initialization, restart, upgrade over the prior version, SQLite migration, printing, keyring/Credential Manager, backup/restore, offline sale and outbox recovery. Record OS build, app hash, test evidence, and signed installer hash. This repository has not yet passed that clean-VM test.

## Update and rollback

Back up the local SQLite database using the application backup feature before upgrade. Install the new signed build over the prior version without deleting `%LOCALAPPDATA%\PharmacyManagementSystem`. If startup or migration fails, stop the app, preserve the failed database and logs, and restore a verified pre-upgrade backup; never replace data with a database from another shop. Keep the previous signed installer available. Uninstall removes application binaries; operators must separately follow the documented data-retention policy before deleting user data.

## Cloud service

The backend declares Python 3.11+ and PostgreSQL 14+; production operating-system/container combinations still need operator qualification. Provision PostgreSQL 14+ and private Redis, TLS certificates, DNS, and a secret manager. Set `APP_ENV=production`, `DATABASE_URL` with PostgreSQL TLS, a random `JWT_SECRET` (32+ bytes), restricted `CORS_ORIGINS`, and `RATE_LIMIT_STORAGE_URI=rediss://...`. Inject Daraja production values only after Safaricom approval. Use separate least-privilege migration and runtime roles; runtime must not be superuser or `BYPASSRLS`.

Build the backend image from a pinned commit and dependency lock/constraints, run migrations as a deployment job (`alembic upgrade head`), then deploy FastAPI behind a trusted TLS reverse proxy. Configure DNS and certificate renewal; accept forwarded headers only from the proxy. Verify `/health/live`, `/health/ready`, tenant isolation, rate limiting, error redaction, and callback reachability before traffic. The repository does not provide a hardened container/orchestrator manifest; the cloud operator must supply and review it.

Use encrypted PostgreSQL base backups plus WAL/PITR and a separately protected key, shared-rate-limit Redis persistence as appropriate, and restore drills according to `BACKUP_AND_RECOVERY.md`. Alert on readiness, error rates, sync conflicts, backup age, payment reconciliation backlog, and TLS expiry. Define incident ownership and Safaricom escalation contacts.

## Cloud rollback and outage recovery

Do not use Alembic downgrade as a production rollback. Roll application code back to the previous compatible image while retaining forward-compatible schema, or restore PostgreSQL to an isolated point-in-time copy and verify before cutover. Preserve all terminal databases and outboxes during cloud outages. Local shops can continue under the documented bounded offline policy; on reconnect, retry stable event IDs, surface conflicts, and reconcile before treating shared inventory as current. Never resolve financial or stock conflicts by overwriting history.

## Required release evidence

Attach the clean Windows VM report, desktop and PostgreSQL test reports, tenant isolation/security scan results, Daraja sandbox evidence, restore drill, backup checks, dependency vulnerability report, signed artifact hashes, and Kenyan privacy/pharmacy compliance review to the release record. Missing evidence blocks pilot/production classification.
