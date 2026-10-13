# Security model and audit findings

## Current security observations

These are source findings, not a penetration-test result.

1. **Recovery path hardened in this stage.** The universal master PIN and fixed reset password have been removed. Recovery requires the shop-configured PIN and caller-chosen 12+ character password. Legacy installations that have no owner PIN cannot use in-app recovery; preserve a verified backup and use a controlled local database recovery procedure. There is still no recovery throttling, so local access permits offline PIN guessing.
2. **Weak password policy.** `logic/auth.py` accepts passwords as short as four characters and has no visible login throttling or lockout. `logic/security.py` uses PBKDF2-HMAC-SHA256 with a random 16-byte salt and 200,000 iterations; malformed hex in a stored hash can raise `ValueError` rather than return a normal failed-login result.
3. **Authorization is largely presentation-level.** Cashier/admin page visibility and action controls are primarily in `ui/`. Many business functions accept record IDs but no authenticated actor/permission context. In the local desktop threat model, a user with filesystem/Python access can modify the database or call logic directly. A future API must enforce authorization server-side on every request and scoped query.
4. **First-run credential hardened in this stage.** No common password is seeded or shown. A unique random credential is generated; development prints it once to the local console and the packaged Windows app displays it once after storing it in Windows Credential Manager. Clean Windows build and restart behavior still need verification.
5. **Audit is best effort.** `logic/audit.py` suppresses every audit failure; callers can proceed without evidence. High-impact actions such as sales, payments, stock intake/adjustment, and login failures do not appear to have a comprehensive append-only audit trail. Restore history is plain text and best effort.
6. **Backups contain all local business data.** No application-level encryption-at-rest or backup access-control policy is implemented. Backup and restore now use SQLite's online backup API and integrity/table validation; OS disk protection and off-device encrypted retention remain operator requirements.
7. **Restore and backup paths need review.** Restore stages and validates a temporary online backup before replacing the active database and makes a safety copy. Safety-copy names can collide within one second. The plain-text restore log is susceptible to local tampering. `delete_backup` trusts the filename in the DB; a modified local database could direct deletion to an unintended path.
8. **Migrations run on startup.** `database/db_manager.py` executes inline `ALTER TABLE` steps and backfills without a migration ledger or pre-migration backup. Some steps commit individually. Interrupted upgrades and restore across versions need explicit handling.
9. **Sensitive data and logs.** Audit details/reports can include customer, doctor, controlled-medicine, and transaction data. Future centralized logs must redact credentials, tokens, customer contact/payment data, and health details.
10. **Shared-computer exposure.** Database and backup paths are local files; disk encryption, OS account separation, screen locking, and Windows permissions are outside current application enforcement.

Positive observations: SQL values are generally passed as bound parameters; login passwords are hashed rather than stored in plaintext; SQLite foreign keys are enabled per connection; checkout and returns wrap multi-row operations in transactions; restore checks database integrity and required tables before replacement.

## Stage 2 cloud security controls

The `backend/` service now implements Argon2 password hashing, short-lived signed access tokens, rotating opaque refresh tokens with reuse-family revocation, invitation flows, request validation, rate limits, redacted request logging, server-side role/permission and tenant/shop checks, subscription checks, device enrollment/revocation, transactional audit events, composite ownership constraints, and forced PostgreSQL row-level security on tenant-owned tables. API integration tests exercise isolation with a non-superuser PostgreSQL role.

Configuration requires a sufficiently long JWT secret. Production configuration requires a Redis rate-limit store to avoid process-local limits across workers. `.env.example` contains placeholders only. Invitation delivery is out of band; device credentials are returned once at enrollment and stored hashed. These controls have not received an independent penetration test or production operations review.

## Target security boundaries

### Actors and credentials

- Human authentication produces a server-validated identity. Use a maintained password hashing scheme with parameterized work factor, strong password policy, rate limiting, session expiry/revocation, and optional MFA for privileged users.
- Device credentials are distinct from human credentials, scoped to one registered device/shop, stored using platform secure storage where available, rotatable, and revocable. Never accept device identity from a request body without validating its credential.
- Daraja secrets remain server-side in secret management/configuration; none belong in desktop binaries or source code. STK success requires callback correlation and payment validation; callback payloads are not cryptographically authenticated by this adapter and must be reconciled with provider records when uncertain.

### Tenant and shop authorization

For every API request: authenticate; resolve the active user; load current membership and entitlement; validate requested tenant/shop scope against that membership; apply scope in every query; record a redacted audit event. The Stage 2 user API follows this boundary. Device enrollment, revocation, and device-authenticated synchronization exist in the backend foundation. Never rely on client roles, hidden UI pages, submitted tenant IDs, or locally cached authorization for online server access.

Use foreign keys and composite constraints to prevent cross-tenant references. Consider PostgreSQL row-level security as defense in depth. Test tenant and shop isolation for reads, writes, exports, sync, administration, and error paths.

### Offline security

- A cloud user is cached only after successful online authentication and explicit tenant/shop selection. The local password verifier is a fresh PBKDF2 hash of the supplied password, not a copy of the server verifier. Offline sign-in expires 72 hours after the last successful online login; a revoked user can therefore retain offline access until reconnect or expiry. Keep this window documented for operators.
- Cloud device credentials are returned at enrollment and kept in the OS keyring. LAN credentials are individually enrolled and stored in the OS keyring. LAN HTTP requests are HMAC-signed with a timestamp checked within five minutes; device revocation blocks subsequent requests. Do not expose the plain HTTP coordinator outside a trusted private LAN; deploy TLS when traffic crosses untrusted networks.
- Offline sales remain durable when no coordinator/cloud is available, but the UI warns that stock may diverge. Event replay is idempotent; stale stock becomes a review conflict rather than being silently overwritten. Conflict notes are audit context only; they do not themselves post a compensating transaction.
- The LAN enrollment secret grants enrollment/revocation authority and must be protected like an administrator credential. Set `SOLONA_LAN_ENROLLMENT_SECRET` to a stable secret kept in a protected service environment; `SPMS_LAN_ENROLLMENT_SECRET` remains a fallback for existing deployments. Otherwise the process prints a newly generated one-time value at startup. Rotate credentials by revoking and re-enrolling terminals.
- Use HTTPS for cloud endpoints. The desktop stores local business data and cached authorization in SQLite without application-level encryption; OS disk encryption and access controls remain necessary. Local admin access cannot equal cloud-enforced security when the machine owner controls the OS and files.

### Data protection and operations

- Use TLS for cloud/LAN communication and encrypt sensitive backups where platform support allows.
- Keep secrets out of logs, desktop config, source control, and crash reports.
- Maintain append-only business/security audit records with actor, scope, event ID, time, and outcome; critical audit writes should be in the business transaction or durable outbox.
- Backups are encrypted, access-controlled, versioned, integrity-checked, retained by policy, and restore-tested. Document RPO/RTO and key recovery.
- Central logs use structured redacted fields, protected access, retention, alerting, and incident response.

## Required security gates before SaaS pilot

- Add recovery rate limiting and independently review the owner PIN recovery model.
- Complete a threat model for device theft, local administrator, cashier, malicious tenant, compromised device, replayed sync, and provider callback.
- Review tenant/shop authorization for every new API resource as it is added.
- Pen-test device-authenticated sync, idempotency and replay protection before production acceptance of offline events.
- Validate authorization and isolation with adversarial automated tests.
- Test backup confidentiality, corruption detection, restore and key recovery.
- Define breach response, data retention/deletion, and Kenyan privacy/legal review before production handling of customer or health-related records.

## Stage 5 release audit

The fixed master recovery credential and common first-run password findings were addressed in source during Stage 5. Recovery now depends on a shop-configured PIN and a newly selected password. Daraja callbacks are not treated as authenticated payment proof; entitlement changes require a correlated provider STK Push Query response. Remaining release concerns include offline PIN guessing without throttling, local database/backup files not being application-encrypted, no external penetration/dependency scan, unverified Windows Credential Manager/package behavior, and no actual Daraja sandbox transaction. Current classification is **INTERNAL TESTING ONLY**; see `TESTING.md` and `DEPLOYMENT.md` for verification evidence and remaining gates.
