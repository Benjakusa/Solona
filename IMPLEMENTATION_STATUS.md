# Implementation status

Last updated: 2026-10-12

## Stage 1 — audit and architecture preparation

| Area | Status | Evidence / note |
| --- | --- | --- |
| Repository structure and entry points | Complete (source inspected) | `main.py`, `ui/`, `logic/`, `database/`, installer and package spec reviewed. |
| Current workflows and SQLite model | Complete (source inspected) | Features and tables described in `ARCHITECTURE.md` and `DATA_MODEL.md`. |
| Security and migration risks | Complete (source inspected) | See `SECURITY_MODEL.md`; no penetration test performed. |
| Python version and declared dependencies | Complete | Python 3.13.7 and `requirements.txt` recorded in `TESTING.md`. |
| Syntax baseline | Complete | `compileall` passed for `main.py`, `database`, `logic`, and `ui`. |
| Runtime and primary workflow baseline | Blocked / untested | `python main.py` stopped at missing PySide6; no declared runtime dependencies are installed. |
| Automated tests | Not available | No test suite/config found and pytest is absent. |
| Local database backup | Not applicable in clone | No `data/pharmacy.db`, `data/`, or `backups/` existed. No backup was fabricated. |
| Git checkpoint | Complete | `stage-1-audit` created from clean `main` at `d192745`. |
| Architecture/data/security/testing docs | Complete | `ARCHITECTURE.md`, `DATA_MODEL.md`, `SECURITY_MODEL.md`, `TESTING.md`. |
| Change log and status | Complete | This file and `CHANGELOG.md`. |

### Stage 1 overall

**Documentation and static audit complete; runtime baseline incomplete.** The Stage 1 desktop workflows were not changed. The desktop runtime baseline remains unverified in this environment.

## Stage 2 — multi-tenant backend foundation

| Area | Status | Evidence / note |
| --- | --- | --- |
| FastAPI/PostgreSQL application structure | Implemented | Versioned API, services, models, schemas, auth, configuration and safe error handling are in `backend/`. |
| Tenant/shop/user/device APIs | Implemented | Tenant registration, shop management, invitations, membership and shop assignment, device enrollment/revocation, summaries. |
| Core pharmacy data model | Implemented | UUID tenant/shop scoped records, purchases, sales, payments, batches, stock movements, audit and sync event entities. |
| Database migration | Implemented | Alembic initial migration with constraints, indexes, seed permissions/plan, and forced tenant RLS. |
| Authentication and authorization | Implemented | Argon2, short-lived access tokens, rotating refresh sessions, explicit permissions and scope validation. |
| PostgreSQL integration tests | Passed | 10 passed against PostgreSQL 14.20 with a disposable non-superuser role; see `TESTING.md`. |
| Desktop sync, M-Pesa, legacy import | Not implemented | Outside Stage 2 foundation; do not proceed to Stage 3 until backend tests pass. |

Stage 2 backend foundation and its requested integration suite are complete. Sync, M-Pesa integration, legacy import, and production operations remain outside this foundation.

## Stage 3 — offline desktop and shop networking foundation

| Area | Status | Evidence / note |
| --- | --- | --- |
| Local SQLite batches, movements, purchases, payments, device/scope identity | Implemented | Transactional additive migration; existing medicine quantities are backfilled into opening batches. |
| Atomic offline sales and durable outbox | Implemented | Local sale, line/batch allocations, payment, movements, and event commit atomically; IDs persist for retries. |
| Offline login policy | Implemented | Cached cloud authorization expires after 72 hours; tenant/shop selected during online login. |
| Cloud sync and authorized change feed | Implemented with gaps | Device-authenticated bootstrap, idempotent push, resumable pull; sale-return replay validates stable sale-line/batch scope and records financial, stock, audit, and inbox effects atomically. Conflicting or legacy-unmapped events remain review-only. |
| Shop LAN coordinator | Implemented with provisioning constraint | SQLite-backed serialized event service, HMAC device auth, enrollment/revocation, stale-stock conflicts; terminals need a common verified starting catalogue. |
| UI sync status and conflict review | Implemented | Shows workspace/device, pending count, last sync/error and review count; reconciliation note does not automatically post correction. |
| Desktop logic tests | Passed | 13 passed in temporary SQLite tests. |
| Backend integration tests | Passed | 12 passed against disposable PostgreSQL 14.20. |
| Full desktop runtime, UI, printer, multi-host outage/recovery | Not verified | PySide6 unavailable in environment; LAN coordinator was service-tested, not exercised across multiple physical terminals. |

Stage 3 foundation is implemented, but production rollout requires the listed provisioning and operational gaps to be closed and multi-computer recovery to be exercised with actual terminals. Do not treat standalone terminals as coordinated inventory while disconnected from a configured shop server.

## Stage 4 — SaaS billing and operations foundation

| Area | Status | Evidence / note |
| --- | --- | --- |
| Daraja subscription STK flow | Implemented as provider adapter | Sandbox/production endpoints, server-side secrets, payment ledger, idempotency key, pending/failed/uncertain states, callback correlation, duplicate detection, and STK status query. Requires Safaricom sandbox and callback testing. |
| Callback payment state | Secure confirmation path implemented; external validation pending | Public callbacks are hints only. Subscription success requires a correlated Daraja STK Push Query response; timeout, mismatch, unknown status, and old unbound attempts remain pending/reconciliation-required. No sandbox transaction has been run. |
| Subscription enforcement | Implemented | Trial, active, past-due, canceled/expired, grace period and plan shop/device limits; expired accounts retain permitted reads and export, and tenant data is retained. |
| Tenant/platform administration | API foundation implemented | Tenant shop/staff/device/billing/export endpoints and metadata-only operator routes, time-limited audited support grant, session/device revocation, incidents, sync health and payment reconciliation. No graphical SaaS admin console. |
| Pharmacy controls | Implemented in backend foundation | Expiry/low-stock reads, expired batch sale prevention, batch inventory, supplier purchase reconciliation and audited stock/payment workflows. Regulatory medicine classification requires Kenyan review. |
| Operations and compliance | Documented; deployment gates remain | See `backend/OPERATIONS.md`. External monitoring, MFA, scheduled reconciliation, production backup/restore drills, TLS/secret service setup, and Safaricom/ Kenyan regulatory review are not completed by repository code. |
| Verification | Static checks passed; integration suite unavailable here | `compileall` and `git diff --check` pass. Backend pytest cannot import because `pydantic` is absent; no disposable PostgreSQL test URL was supplied. |

## Pending work (future stages)

- Implement signed LAN snapshot provisioning and automatic client bootstrap.
- Implement automated conflict compensating workflows and test long-outage/reconnect scenarios.
- Complete production TLS/secret-manager deployment, MFA, external monitoring and scheduled reconciliation.
- Complete clean Windows package and clean-machine upgrade/restore validation.
- Complete Safaricom sandbox and production approval process, restore drills, security scans, end-to-end integration, and Kenya regulatory review.

## Stage 5 — integration, security audit, and release preparation

| Area | Status | Evidence / note |
| --- | --- | --- |
| Release-blocking local recovery flaw | Fixed in source | Removed fixed master PIN/default reset password; recovery requires configured owner passphrase and new 12+ character password. No in-app recovery is available if legacy shops never configured a passphrase. |
| First-run local admin credential | Fixed in source | Unique random secret; development prints once, packaged Windows path temporarily stores it in Credential Manager and displays once. Windows behavior not exercised. |
| SQLite backup/restore integrity | Hardened in source | Uses SQLite online backup and validates staged snapshots. Automated restore test added; suite not runnable here because pytest is missing. |
| Automated desktop tests | Passed in isolated temporary data | 15 tests passed after the P1 return-event mapping change. |
| Backend integration/security tests | Passed in disposable PostgreSQL | 16 tests passed on PostgreSQL 14.20 with a non-superuser test role; includes the new sale-return replay and migration tests. |
| Static verification | Passed | `python -m compileall -q backend/app backend/tests` and `git diff --check`. |
| Security scanning / adversarial review | Incomplete | No dependency scanner or external penetration test was available. Tenant isolation has prior integration-test evidence recorded in `TESTING.md`, but not rerun in this audit. |
| Windows package / clean-machine test | Not run | Current host is Linux; no Windows VM/runner or Inno Setup validation evidence is available. |
| Full e2e, Daraja sandbox, recovery/restore drill, regulatory verification | Not run | Requires disposable PostgreSQL, Safaricom sandbox access, Windows test machine, restore target, and Kenyan compliance review. |
| Release decision | **INTERNAL TESTING ONLY** | Not pilot-ready or production-ready. Sandbox payment validation, full tenant-isolation and sync-conflict/recovery evidence, Windows deployment, backup restore drills, and regulatory/operational gates remain. See `DEPLOYMENT.md` and `BACKUP_AND_RECOVERY.md`. |

These are plans, not completed capabilities.
