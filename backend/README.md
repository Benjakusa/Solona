# Backend API

This FastAPI service powers the SOLONA Pharmacy web app. It uses PostgreSQL and exposes its versioned API under `/api/v1`. The browser PWA caches selected data locally and submits offline business events through the device sync API.

## Local setup

Requires Python 3.11+ and PostgreSQL 14+. Check the interpreter before installing: some systems have Python 3.8 as `python` or `pip`, which is too old for this backend. Create a dedicated empty database and a non-superuser application role. From the repository root:

```bash
python3.11 --version
python3.11 -m venv backend/.venv
source backend/.venv/bin/activate       # Windows: backend\.venv\Scripts\activate
python -m pip install -r backend/requirements.txt
cp backend/.env.example backend/.env
```

On Windows, use `py -3.11 -m venv backend\.venv` and `backend\.venv\Scripts\activate`. If `python3.11` is not installed, install Python 3.11 or newer first. Use `python -m pip`, not bare `pip`, after activating the environment so pip installs for the selected interpreter.

Edit `.env` locally. Set `DATABASE_URL` to the PostgreSQL DSN for the dedicated backend database. Generate a random `JWT_SECRET` with at least 32 bytes of entropy. Keep `.env`, database passwords, Redis credentials, and production credentials out of version control and logs. `backend/.gitignore` excludes `.env`.

Apply the schema and start the service:

```bash
alembic upgrade head
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Interactive API documentation is at `http://127.0.0.1:8000/api/v1/docs`; OpenAPI JSON is at `/api/v1/openapi.json`. `/health/live` checks the process; `/health/ready` checks the database.

## Production configuration

- Use TLS at the service boundary and configure PostgreSQL TLS (`sslmode=require` or stronger). Restrict database network access.
- Use separate migration and runtime database roles. The runtime role must not be a superuser or have `BYPASSRLS`; RLS is forced on tenant-owned tables. The runtime role should have only required DML privileges.
- Set `APP_ENV=production`, a strong secret manager sourced `JWT_SECRET`, a restricted comma-separated `CORS_ORIGINS`, and `RATE_LIMIT_STORAGE_URI=rediss://...`. Startup configuration rejects in-memory rate-limit storage in production. Redis must be private and credential protected.
- Terminate TLS at a trusted reverse proxy. The limiter keys on the connecting IP; configure proxy trust deliberately and do not accept arbitrary forwarded client-IP headers.
- Access tokens last 10 minutes. Refresh credentials are opaque, hashed at rest, rotated on use, and revoked as a family if a rotated token is reused. Store refresh credentials in an appropriately protected client store.
- Tenant registration provisions one 14-day trial, an owner membership, the initial shop, and default roles. Invitations return a one-time token because no email delivery integration exists; deliver it through a secure out-of-band channel. Existing users must authenticate before accepting another tenant invitation.
- Subscription billing uses a separate ledger and does not mutate retail sale payments. Configure `DARAJA_*` secrets in the environment/secret manager; never commit credentials. STK initiation remains pending until a correlated callback validates the checkout request, amount, phone, and receipt. Repeated idempotency keys do not initiate another prompt. Ambiguous timeouts are marked for reconciliation. A status-query response alone does not mark a subscription paid.
- Start in `DARAJA_ENVIRONMENT=sandbox` with Safaricom-provided test credentials and simulator data. Callbacks require a public callback URL reachable by Safaricom; production requires HTTPS. Production credentials, shortcode/till/paybill configuration, Daraja product enablement and Safaricom go-live approval must be obtained directly from Safaricom before switching environments.
- Daraja callbacks do not provide an application-verifiable signature in this adapter. The handler correlates provider IDs and validates amount/phone/receipt, deduplicates callback payloads, and flags mismatches. Restrict the public endpoint with rate limits and TLS; reconcile exceptional cases against Safaricom's records. Never infer payment success from a prompt request or customer statement.
- Billing read access remains available during expiry. Subscription-protected writes, shop/device additions, and other plan-limited operations are denied after the configured grace period. Tenant records are retained; tenant owners can authenticate and export authorized data. Local offline pharmacy operations are governed by the web client's explicit offline policy in the root README.
- Provision platform operators manually for an existing verified account with `python -m app.ops grant operator@example.com`; revoke with `python -m app.ops revoke operator@example.com`. These operators receive a metadata-only console API by default. Tenant audit details require an explicit reasoned support grant that expires within 60 minutes and are logged. No endpoint grants unrestricted access to tenant clinical/commercial records.
- Device enrollment stores a hash and returns the credential once. Tenant admins and platform operators can revoke devices; platform operators can revoke account sessions. Production platform/operator accounts should use a hardened identity and MFA layer before go-live; MFA is not yet implemented here. See `OPERATIONS.md` for outage policy, restore drills, and Kenya deployment gates.

## Tenant isolation

Every tenant business table carries `tenant_id`; shop operations additionally carry `shop_id`. Composite foreign keys prevent cross-tenant and cross-shop references. Application authorization checks active membership, permission, shop assignment, and active subscription. PostgreSQL RLS adds a second boundary using the transaction-local `app.tenant_id` set only after choosing a requested scope; the application still checks membership and permissions. Never treat a path/body tenant or shop UUID as authority.

## API outline

- `POST /api/v1/auth/register-tenant`, `/login`, `/refresh`, `/logout`, `/accept-invitation`
- Tenant/shop/staff/device configuration, summaries, billing, support incidents, and tenant data export under `/api/v1/tenants/{tenant_id}`
- Daraja subscription STK Push, callback, and reconciliation endpoints under `/api/v1/payments` and `/api/v1/tenants/{tenant_id}/billing`
- Operator-only tenant/subscription/device/payment/support/synchronization metadata under `/api/v1/platform`; manually provisioned through `app.ops`
- Medicine catalogue, supplier/customer, purchase receipt, batch inventory, expiry/low-stock alerts, sales and payments under `/api/v1/tenants/{tenant_id}`

Validation failures omit submitted input values so passwords and credentials are not echoed. Error responses do not include database constraint details. Request logs include request ID, method, path, status, and duration; they do not log bodies, authorization headers, or tokens.

## Migrations, backup, recovery

`alembic upgrade head` applies the schema, plans, permissions, and forced RLS policies. Treat migration revisions as immutable. Before production rollout, schedule encrypted PostgreSQL base backups and WAL/PITR, run integrity checks, restore into an isolated database, verify tenant exports, and rehearse migration from a restored copy. Do not run Alembic downgrade as a production rollback: it drops prior schema objects. Production recovery uses restore/PITR and a forward fix. Tenant export is a JSON logical export and is not a replacement for a tested database restore. The desktop SQLite data is not imported automatically; conversion requires a separately tested, reconciled migration before any tenant pilot.

Expiry/low-stock alert reads are available per authorized shop. Sales reject expired batches, and batch quantities remain transactional. The API requires sale callers to provide batch IDs; clients should present batches by earliest eligible expiry. Prescription and restricted-medicine workflow classification must be configured against the applicable PPB schedule and reviewed by a Kenyan pharmacy compliance professional before production; a generic `is_controlled` catalogue flag does not by itself establish legal classification.

Use `/health/live` and `/health/ready` behind an HTTPS reverse proxy. Request logs include method, path, status, request ID, and duration only. External error-monitoring integration, deployment-specific alerting, MFA, email delivery, automated payment reconciliation jobs, and production backup/restore drills remain deployment prerequisites.

The initial revision is a bootstrap migration generated from the Stage 2 model definitions. Later schema changes must be committed as new Alembic revisions; do not edit the applied initial revision.

## Integration tests

Tests use Alembic to drop and recreate their database. Provide a **disposable** PostgreSQL URL whose database name ends in `_test` or `_tests`:

```bash
export TEST_DATABASE_URL='postgresql+psycopg://test_user:test_password@localhost:5432/pharmacy_backend_test'
pytest -q
```

The test guard refuses other database names. Never point `TEST_DATABASE_URL` at production or a database containing retained data. The suite covers tenant/shop scope, membership assignments, batch receipt/sale, transaction rollback, duplicates, concurrent stock deduction, database constraints, and migration down/up recovery.
