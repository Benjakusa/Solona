# Shani Pharmacy SaaS: five-stage implementation verification audit

**Audit date:** 2026-10-12  
**Repository:** `/home/benjakusa/BLE/Pharmacy-Desktop-Application`  
**Scope:** read-only source, configuration, migration, route, UI, test, deployment and Git inspection; safe tests used isolated temporary SQLite data only. No production database, live payment, credentials, patient records, code changes, or destructive database tests were used.

## A. Executive summary

The project is **still a Windows-oriented desktop pharmacy application with a partially converted SaaS backend**. It has a substantive FastAPI/PostgreSQL backend source tree, tenant/shop/device models, a desktop outbox and synchronization client, an optional SQLite shop coordinator, and a Daraja subscription payment adapter. That is not equivalent to a functionally integrated, operationally proven offline-first SaaS.

The current audited state is **internal testing only**. Pytest could not run because it is absent; the backend app and PostgreSQL migration path could not be started; no Daraja sandbox, Windows runner, clean installation, printer, production-like restore drill, vulnerability scanner, or regulatory reviewer was available. Source review found two release-critical correctness/security defects: a duplicate `record_replayed_sale` definition that shadows the implementation handling discounts/tax and client sale-line IDs; and a public Daraja callback that can mark subscription payment successful without verifying the callback with Safaricom. A forged callback can grant subscription time without a verified charge. These findings alone prevent a controlled pilot or production release.

The working tree contains extensive modified and untracked Stage 2–5 work, while `HEAD` remains commit `defafaf` (`feat: add tenant-aware pharmacy backend foundation`) on branch `stage-3-offline`. There is no Stage 4/5 release commit or tag in the inspected history. Documentation and prior test-result claims were treated as claims, not test evidence.

## B. Overall scorecard

Counts use the audit request's actual checkbox totals: Stage 1 **13**, Stage 2 **30**, Stage 3 **40**, Stage 4 **28**, Stage 5 **43** (154 total). The item-by-item statuses below are the source of these totals.

| Stage | Total items | Pass | Partial | Fail | Not verified | Blocked |
|---|---:|---:|---:|---:|---:|---:|
| Stage 1 | 13 | 2 | 9 | 0 | 1 | 1 |
| Stage 2 | 30 | 8 | 13 | 1 | 0 | 8 |
| Stage 3 | 40 | 6 | 22 | 0 | 3 | 9 |
| Stage 4 | 28 | 9 | 17 | 2 | 0 | 0 |
| Stage 5 | 43 | 5 | 14 | 2 | 1 | 21 |

Status meaning follows the request. **PASS** requires implementation plus relevant test evidence. **PARTIAL** means evidence exists for only part of the criterion. **BLOCKED** is used where the requested test could not run due to the environment. **NOT VERIFIED** means no adequate implementation or test evidence was found. **FAIL** means source evidence demonstrates a missing or incorrect requirement.

## C. Detailed checklist

Evidence paths below are repository-relative. For source checks, “method/result” means static source tracing unless an actual isolated smoke test is named. Pytest source files are evidence that tests were authored, not that those tests passed in this audit.

### Stage 1 — architecture, baseline and stabilization (13 items)

| # | Requirement | Status | Evidence, verification/result, risk and recommended action |
|---|---|---|---|
| 1.1 | App starts in supported environment | BLOCKED | `main.py` imports PySide6 and invokes a Windows named mutex (`ctypes.windll`); PySide6 is absent and host is Linux. `python main.py` was not run because import failure is known from dependency check. Clean Windows startup still needs execution. |
| 1.2 | Workflows identified and tested | PARTIAL | Desktop modules cover inventory, sales, customers, suppliers, returns, reports, users, settings, backup; `tests/test_offline_foundation.py` and `tests/test_shop_coordinator.py` cover selected logic. Current pytest run is blocked; receipt/UI and complete everyday workflow are unproven. Run full tests and Windows workflow matrix. |
| 1.3 | Python/dependencies documented and reproducible | PARTIAL | `requirements.txt`, `requirements-dev.txt`, `backend/requirements.txt`, and `TESTING.md` exist; Python 3.13.7 observed. Dependencies use ranges and no complete lock/constraints file was found. Generate and validate reproducible dependency locks. |
| 1.4 | UI/business/persistence architecture documented and separated | PASS | `ui/`, `logic/`, `database/` and `backend/app/{api,services,models,db}` exist; `ARCHITECTURE.md` describes the split. This is a structural pass, not a claim that all screens use clean service boundaries. |
| 1.5 | Backup/restore procedures exist and are tested | PARTIAL | `logic/backup.py`, `ui/backup_view.py`, `BACKUP_AND_RECOVERY.md`; isolated smoke created a temp DB, backup, changed stock, restored, and verified `PRAGMA integrity_check=ok`. No packaged Windows, multi-process, cloud PITR, or restore drill evidence. Add tests for corrupted restore, active WAL, and real packaged paths. |
| 1.6 | Migration/safe schema-evolution strategy exists | PARTIAL | Cloud Alembic revisions exist; desktop `database/db_manager.py` has inline additive migrations and one version-3 transaction. Several older `_add_column_if_missing` calls commit separately and there is no complete migration ledger for the legacy changes. Rehearse upgrades from representative legacy copies. |
| 1.7 | `ARCHITECTURE.md` accurately reflects source | PARTIAL | It correctly describes SQLite/offline/outbox/cloud and calls cloud returns review-only. It also has target/proposed sections and some statements are not demonstrated operationally; repeated `record_replayed_sale` source is material to the sync description. Update after defects are resolved and tested. |
| 1.8 | `DATA_MODEL.md` accurately reflects schemas | PARTIAL | Desktop schema and SQLAlchemy models have distinct batches, payments, stock movements and tenant entities. Cloud return replay is indeed absent. Model/docs do not provide proof that every stated relationship/workflow passed migrations; migration execution was blocked. Compare generated PostgreSQL schema and SQLite `sqlite_master` in tests. |
| 1.9 | `SECURITY_MODEL.md` reflects implemented controls | PARTIAL | It records local-file limits, offline auth, RLS, sync, and callback-signature limitation. Some statements describe target controls and security assurance beyond tests actually run. Separate source facts from deployment assumptions and penetration-test results. |
| 1.10 | `TESTING.md` gives reproducible commands and actual coverage | PARTIAL | Commands and named coverage exist. It contains historical pass counts that were not independently reproducible in this audit; current test commands fail before collection due missing pytest. Keep historic results labeled and attach run artifacts/commit/database version. |
| 1.11 | `IMPLEMENTATION_STATUS.md` is accurate | PARTIAL | It records several blockers, but prior Stage 2/3/4 completion and test claims are not verifiable from this current environment; Stage 4 files are uncommitted. Reconcile status to a tagged commit and CI evidence. |
| 1.12 | No existing pharmacy functionality unintentionally removed | NOT VERIFIED | Large modified diff touches auth, sales, inventory, suppliers and UI; there is no runnable regression suite or full baseline comparison. Run before/after regression tests on synthetic legacy DBs and core UI workflows. |
| 1.13 | Critical baseline failures/technical debt documented | PASS | `SECURITY_MODEL.md`, `TESTING.md`, `IMPLEMENTATION_STATUS.md` document offline/local-file, security, runtime and deployment limitations. Continue carrying newly found duplicate replay and callback-trust defects into the release blocker list. |

### Stage 2 — multi-tenant backend and database (30 items)

| # | Requirement | Status | Evidence, verification/result, risk and recommended action |
|---|---|---|---|
| 2.1 | FastAPI exists and starts | BLOCKED | `backend/app/main.py`; import requires settings and dependencies. `pydantic` is absent, so backend tests fail at `conftest.py` import; no configured test URL was used. Install pinned deps in an isolated environment and start against disposable PostgreSQL. |
| 2.2 | PostgreSQL central DB configured | PASS | `backend/app/core/config.py` validates `postgresql+psycopg://`; `backend/app/db/session.py` creates SQLAlchemy engine. No production connection attempted. |
| 2.3 | Clean DB migrations work | BLOCKED | Alembic revisions `0001`–`0004` exist; `backend/tests/test_api_integration.py::test_migration_downgrade_and_recovery` is authored. No disposable database/dependencies available, so clean upgrade was not run. Run isolated PostgreSQL migration test. |
| 2.4 | Routes/services/models/auth appropriately separated | PASS | `backend/app/api/v1/`, `services/`, `models.py`, `api/deps.py` provide those layers. Source structure verified. |
| 2.5 | Input validation/error handling | PASS | `schemas.py`, `core/errors.py`, request validation handler in `app/main.py`; source rejects malformed fields and avoids returning validation inputs. No fuzz run. |
| 2.6 | Constraints/FKs/indexes appropriate | PARTIAL | `models.py` contains tenant composite FKs, checks, unique constraints and indexes. Not every invariant is covered, and migration-created schema equivalence was not tested. Verify Alembic output and constraint tests. |
| 2.7 | API docs match endpoints | PARTIAL | FastAPI exposes generated `/api/v1/openapi.json` and docs; no static OpenAPI snapshot or live app was available for comparison. Generate and contract-test OpenAPI. |
| 2.8 | Independent tenants create/manage | BLOCKED | `auth.register_tenant`, tenant routes, and multi-tenant test exist. Test execution blocked; not operationally confirmed. Run registration and cross-scope flows on disposable PostgreSQL. |
| 2.9 | Multiple shops per tenant | PARTIAL | `tenants.create_shop` and `max_shops` logic; multi-shop tests authored. DB behavior not run; one-shop fixture paths have limited evidence. Run multi-shop integration. |
| 2.10 | Explicit memberships/shop permissions | PARTIAL | `TenantMembership`, `ShopAssignment`, roles/permissions and tenant routes exist. Tests authored but blocked; role privilege matrix not exhaustive. Test every permission and assignment combination. |
| 2.11 | Device tied to tenant/shop | PARTIAL | `Device` composite ownership and registration endpoint. Test exists but not run. Validate foreign key and forged tenant/shop headers under PostgreSQL. |
| 2.12 | Tenant records have ownership relationships | PARTIAL | Most business models include `tenant_id`; shop rows include `shop_id` and composite FKs. Full schema/migration audit not run; confirm all business tables and cascades. |
| 2.13 | Server-side tenant isolation | PARTIAL | `authorize_tenant`, explicit scoped queries and forced RLS policies in `0001_initial.py`/`0004_saas_billing_ops.py`. Implementation exists, but no current DB test. Test with non-superuser, non-BYPASSRLS role. |
| 2.14 | Cross-tenant changed IDs denied | BLOCKED | `test_two_tenants_and_multiple_shops_are_isolated` and composite constraints cover cases in source; pytest unavailable. Execute adversarial read/write/export/sync ID substitution tests. |
| 2.15 | Cross-shop unauthorized requests denied | BLOCKED | `authorize_shop` and `test_shop_assignment_limits_user_and_summary_scope`; test not run. Add route-wide IDOR matrix and run. |
| 2.16 | RLS implemented and tested | BLOCKED | Forced RLS policies are created in migrations; test source checks RLS catalog and tests non-superuser conditions. Migration/test could not run. Verify all tenant tables, policies and operator exception paths in PostgreSQL. |
| 2.17 | Admin access does not silently bypass isolation | PARTIAL | `require_platform_operator` sets transaction-local operator scope; support endpoints require reason/expiry and audit; no unrestricted business-record endpoint observed. RLS exception behavior and full route inventory untested. Audit every platform route with negative tests. |
| 2.18 | Medicine separate from batches | PASS | `Medicine` and `MedicineBatch` separate SQLAlchemy models; local `medicine_batches` table too. Source verified. |
| 2.19 | Multiple batches coexist | PARTIAL | Batch uniqueness is by tenant/shop/medicine/batch number; purchase service supports separate batch rows. DB tests exist but not run. |
| 2.20 | Batch number/expiry/quantity/prices tracked | PASS | `MedicineBatch` includes these fields and quantity/pricing checks; source verified. |
| 2.21 | Auditable stock movements | PARTIAL | `StockMovement`, transactional receipt/sale functions and audit records exist. Reversal/adjustment workflows and immutable enforcement are not complete or DB-tested. |
| 2.22 | Sales preserve historical prices/costs | PASS | `SaleItem` snapshots unit price/cost; `test_batch_stock_sale_and_historical_cost_are_preserved` authored. Test not run in this audit; source implementation directly stores snapshots, but passing evidence is source-level only. |
| 2.23 | Purchase/sale/payment/return/audit relationships | FAIL | Purchase/sale/payment/audit models exist, but no cloud return API/service/replay path was found; sync routes route unsupported event types to review. Implement and test cloud return/reversal before claiming full relationship workflow. |
| 2.24 | Passwords hashed | PASS | Backend `core/security.py` uses Argon2; desktop `logic/security.py` hashes passwords with PBKDF2. Source inspected; no cracking/config scan performed. |
| 2.25 | Access/refresh token handling | PASS | `auth.py` issues short access tokens and rotating opaque refresh tokens; token hashes stored; source inspected. |
| 2.26 | Expiration/revocation implemented and tested | BLOCKED | Session expiry/revocation and refresh-reuse test exist, but tests could not execute. Run expiry, logout, rotation-reuse, account revoke tests. |
| 2.27 | Sensitive endpoint rate limits | PARTIAL | SlowAPI limits appear on login, invitation, billing/callback/support endpoints; no exhaustive per-route DoS/security review or shared Redis run was performed. Review all sensitive routes and production Redis. |
| 2.28 | Secrets not committed | PARTIAL | Tracked `.env.example` contains placeholders; configuration uses SecretStr/environment values. No complete secret scanner/history scan was run. Run gitleaks/trufflehog on full history before release and rotate any finding. |
| 2.29 | Authorization tests cover tenant/shop | BLOCKED | Relevant test functions are present in backend tests; current test runner cannot import dependencies. Run with disposable PostgreSQL and retain CI evidence. |
| 2.30 | Transactions prevent partial operations | PARTIAL | Service functions use transaction-scoped DB session and row locks; tests for rollback/concurrent stock are authored. Runtime tests unavailable; verify nested rollback and all rows/outbox atomically. |

### Stage 3 — offline-first desktop and synchronization (40 items)

| # | Requirement | Status | Evidence, verification/result, risk and recommended action |
|---|---|---|---|
| 3.1 | Desktop works without internet | PARTIAL | Local SQLite paths and services do not require cloud during local sales; no full app/UI outage test could run (PySide6 absent). Verify offline launch and every needed workflow on Windows. |
| 3.2 | Cash sale offline | PASS | Isolated smoke initialized temp SQLite, checked out a cash sale without network, and confirmed stock decreased. Does not prove complete UI or printer flow. |
| 3.3 | Local sale survives app restart | PARTIAL | Smoke reopened a SQLite connection and found sale/outbox; it did not terminate/restart the application process. Add process restart/crash test. |
| 3.4 | Receipt prints offline | BLOCKED | `logic/printing.py`/receipt UI calls local Windows printer; no printer or Windows host. Test supported printer models offline. |
| 3.5 | Stock deduction atomic with sale | PASS | `logic/sales.py::checkout` transaction; smoke confirmed sale and decreased stock together. Force disk/outbox failures in automated test as well. |
| 3.6 | Inventory/history available offline | PARTIAL | Local inventory and sale query modules use SQLite. Full screen availability/data integrity not tested. Verify each screen with networking disabled. |
| 3.7 | Offline authentication policy secure/documented | PARTIAL | `logic/sync.py` caches auth for 72 hours; `logic/auth.py` checks expiry. Cache is in local SQLite and UI role gating is not a hardened local security boundary. Review local attacker model and test. |
| 3.8 | Offline permissions/session expiry respected | PARTIAL | Cached permissions/expiry are implemented in auth/sync paths; test `test_expired_offline_authorization_is_denied_and_scope_is_fixed` authored but not run. Permission-level UI/action tests missing. |
| 3.9 | Eligible local transactions create durable events | PARTIAL | Sales, purchases, inventory events and returns call outbox in several code paths; not every transaction type has a supported server replay. Enumerate event coverage and test all event producers. |
| 3.10 | Sale/outbox atomic | PASS | `checkout` inserts outbox in its DB transaction; isolated smoke found durable sale/outbox; authored rollback test injects outbox failure. Run the failure test with pytest. |
| 3.11 | Event IDs unique | PASS | UUID generation plus `sync_outbox.event_id` primary key. Source and SQLite schema verified. |
| 3.12 | Events survive restart | PARTIAL | SQLite persistence and reconnect smoke observed event across connection reopen; computer/process restart not simulated. |
| 3.13 | Failed sync retries safely | PARTIAL | `record_delivery_failure` uses bounded backoff and stable event IDs; no live timeout/retry test was run. |
| 3.14 | Duplicate delivery cannot duplicate sale | PARTIAL | Cloud inbox deduplicates event/transaction IDs; backend test exists but unrun. Duplicate local apply test is authored. Run same IDs and altered-content attack. |
| 3.15 | Timeout does not assume remote failure | PARTIAL | Cloud sync catches errors and retains/retries event IDs; no request timeout injection test. Verify response-lost-after-commit case. |
| 3.16 | Checkpoints persist | PARTIAL | `sync_checkpoints` and pull cursor updates exist; no interruption-between-apply/checkpoint test. |
| 3.17 | Remote changes apply safely | PARTIAL | Downloaded changes are idempotent and some local pending edits flag conflict. Dynamic update fields mostly use allowlists; no full feed test. |
| 3.18 | Partial synchronization handled | PARTIAL | Events are individually acknowledged; feed checkpoint updates after applying changes. No crash/process kill at each boundary test. |
| 3.19 | Conflicts recorded/exposed | PARTIAL | `sync_conflicts` plus `SyncConflictsDialog`; server stale stock returns conflict. UI and persistence not run. |
| 3.20 | Financial data not last-write-wins | PARTIAL | Sale/payment records are generally append-oriented; return replay is unsupported and the duplicated replay implementation is incorrect for discounts. Require compensating-record policy and tests. |
| 3.21 | Historical prices/costs stable | PARTIAL | Sale item snapshots exist; catalogue updates affect medicine/batches, not prior sale-item snapshots by design. No update-after-sale test executed. |
| 3.22 | Shop coordinator exists if required | PASS | `network/shop_server.py::ShopCoordinator` stores SQLite events, authenticates devices and serializes processing. Source and direct unit tests exist; service execution not repeated here. |
| 3.23 | Multiple computers connect | BLOCKED | Coordinator uses HTTP server and device HMAC; tests exercise direct service rather than physical/multi-host network. No multi-computer test environment. |
| 3.24 | Concurrent sales controlled | PARTIAL | `BEGIN IMMEDIATE`/locks and concurrency tests exist for local/cloud paths; pytest unavailable and no physical multi-device run. |
| 3.25 | Inventory visible to connected terminals | PARTIAL | Feed/bootstrap logic exists; no two-terminal network visibility test. |
| 3.26 | Coordinated shop works during internet outage | PARTIAL | LAN path is distinct from cloud path; no internet outage/failure injection test. |
| 3.27 | Shop server failure detected/handled | PARTIAL | sync exceptions record status/error; local operations continue, but failure recovery and operator alerts were not exercised. |
| 3.28 | Fallback does not claim coordinated stock | PARTIAL | Main window exposes coordination mode/pending/conflict status; terminal local sales can proceed on loss and may conflict. No UI assertion under server outage. |
| 3.29 | Reconnection/reconciliation processes events | PARTIAL | Stable event retries and coordinator/cloud replay code exists; no prolonged outage/reconnect test. |
| 3.30 | Internet disconnected during sale tested | BLOCKED | Isolated sale smoke had no cloud configured but did not simulate a disconnect in an online-linked client. Run Windows network-cut test. |
| 3.31 | App crash with pending events tested | NOT VERIFIED | No process-kill/reopen test found. Add crash injection around commit/send/ack. |
| 3.32 | Computer restart with pending events tested | NOT VERIFIED | No reboot or VM snapshot test found. Add reboot recovery scenario. |
| 3.33 | Repeated submissions tested | BLOCKED | Backend duplicate event test is authored but suite could not run. |
| 3.34 | Cloud outage/recovery tested | NOT VERIFIED | No end-to-end cloud outage recovery test found. Add API unavailable/restore harness. |
| 3.35 | Duplicate sync tested | BLOCKED | Local/cloud idempotency tests exist in source but pytest did not run. |
| 3.36 | Simultaneous same-batch sales tested | BLOCKED | Local/backend concurrency test functions exist but were not executed. |
| 3.37 | Stale stock tested | BLOCKED | `test_device_sync_flags_stale_stock_and_rejects_revoked_devices` is authored but not executed. |
| 3.38 | Conflicting catalogue tested | BLOCKED | `test_catalogue_download_is_idempotent_and_flags_local_pending_edit` is authored but not executed. |
| 3.39 | Unauthorized device tested | BLOCKED | Device revocation/signature tests are authored but not executed. |
| 3.40 | Local backup/restore tested | PASS | Isolated temp DB backup/restore and integrity smoke passed in this audit. Windows packaging and crash recovery remain untested. |

### Stage 4 — M-Pesa, subscriptions and administration (28 items)

| # | Requirement | Status | Evidence, verification, risk and recommended action |
|---|---|---|---|
| 4.1 | Real Daraja module | PARTIAL | `backend/app/services/daraja.py` makes HTTP OAuth/STK/status requests. No Safaricom sandbox transaction was run, so provider operation is unproven. |
| 4.2 | Sandbox/production separated | PARTIAL | `DARAJA_ENVIRONMENT` selects host and validates values; actual credential separation/deployment not verified. Test with Safaricom sandbox, then separate production secrets. |
| 4.3 | STK request construction correct | PARTIAL | Payload fields, timestamp/password, phone and shortcode are built in `initiate_stk`; no provider contract/sandbox validation. |
| 4.4 | Payment associated with correct tenant and sale | FAIL | Implemented flow is subscription-only (`SubscriptionPayment` linked to tenant/subscription), not retail sale; no tenant-sale Daraja tender flow in desktop/API. Implement if required for sales and scope it transactionally. |
| 4.5 | Callback validates transaction identifiers | PARTIAL | Checkout/optional merchant ID, amount, phone and receipt format checked. Callback authenticity is not verified, and merchant ID is optional. Verify with trusted provider status/reconciliation before entitlement change. |
| 4.6 | Duplicate callbacks cannot duplicate payment | PARTIAL | Callback digest and ledger status prevent repeated subscription extension; different success receipt moves to reconciliation. No executed tests and callback spoof still permits false payment. |
| 4.7 | Pending/success/fail/cancel/unknown states | PARTIAL | Pending/paid/failed/reconciliation_required and cancelled model value exist; callback treats all nonzero as failed and unknown outcomes are not consistently represented. Implement a complete state machine. |
| 4.8 | Timeout leads to reconciliation, not assumed failure | PARTIAL | STK request timeout sets reconciliation_required after persisted attempt; no request timeout test. |
| 4.9 | Initiation is not payment proof | PASS | STK initiation returns pending; status-query success does not itself mark paid. Source verified. The callback trust flaw is separately a release blocker. |
| 4.10 | Real credentials stored securely | PASS | Settings use `SecretStr` and env/secret injection; no secrets in client module. Full history secret scan not run. |
| 4.11 | Pending/failed payments reconciled safely | PARTIAL | Authenticated reconcile route calls STK status query; it only marks nonzero provider result failed and relies on unauthenticated callback for success. Provider-backed success reconciliation is missing. |
| 4.12 | No mock/hard-coded success masquerades as payment | FAIL | Production adapter is real HTTP, but `process_callback` trusts unsigned public JSON and sets `paid`; an attacker able to submit correlated callback data can create a false subscription renewal. Require provider-verified status/transaction before paid. |
| 4.13 | Subscription plans exist | PASS | `SubscriptionPlan` model, API endpoints and migration exist. Runtime not DB-tested here. |
| 4.14 | Tenant subscription status persisted | PASS | `TenantSubscription` model and authorization helpers persist/use state. No current DB run. |
| 4.15 | Trial/expiry/renewal handled | PARTIAL | Trial dates and access checks exist; renewal extends a hard-coded 30 days and no scheduled expiry lifecycle/job was found. Align term to plan and test time boundaries. |
| 4.16 | Shop/device limits enforced | PASS | Shop creation and device registration compare active counts to plan fields server-side. Tests not run. |
| 4.17 | Subscription payments auditable | PASS | Payment/attempt rows plus tenant audit events; provider ledger is separate from retail payments. Tests/provider not run. |
| 4.18 | Suspension/grace behavior | PARTIAL | `require_subscription` handles status/end/grace; operator can patch status/grace. No recovery workflow/job or boundary tests run. |
| 4.19 | Expiry does not delete business data | PASS | No expiry deletion path found; tenant data export/read paths remain. No destructive operational test needed or run. |
| 4.20 | Subscription consistently enforced | PARTIAL | Checks appear on key create/write routes, but route-wide enforcement audit and tests were not completed. Exercise every mutating endpoint and sync behavior after expiry. |
| 4.21 | Platform admin privileges protected | PASS | `require_platform_operator` checks explicit operator row; metadata APIs are protected. MFA absent and privilege tests blocked. |
| 4.22 | Tenant admins restricted to authorized shops/users | PARTIAL | Tenant membership, role and shop assignment checks present. Tests authored but not run; check every endpoint and role. |
| 4.23 | Device registration/revocation works | PARTIAL | Enrollment/revocation routes and hashed credentials exist; no runtime test in this audit. |
| 4.24 | Support access limited/audited | PASS | Grant requires reason and short expiry; tenant audit endpoint omits free-form details and records access. Source verified; no DB execution. |
| 4.25 | Tenant reports correctly scoped | PARTIAL | Summary/export routes scope by memberships; comprehensive tenant sales/stock reports are not implemented in SaaS API. |
| 4.26 | Cross-shop reports only authorized shops | PARTIAL | Summary applies shop assignments; no full consolidated report endpoint or live authorization test. |
| 4.27 | Logs avoid secrets/sensitive data | PARTIAL | API middleware logs path/method/status/request ID, not body or auth header. Local `sync_identity.last_error` stores exception text; no log redaction scan or external monitoring test. |
| 4.28 | Backup/recovery documented and tested | PARTIAL | `backend/OPERATIONS.md`/`BACKUP_AND_RECOVERY.md`; only local SQLite smoke passed. Cloud PostgreSQL PITR restore not demonstrated. |

### Stage 5 — integration, security and release readiness (43 items)

| # | Requirement | Status | Evidence, verification, risk and recommended action |
|---|---|---|---|
| 5.1 | Tenant registration | BLOCKED | `/auth/register-tenant` and test helper exist; backend runtime/test unavailable. Run on disposable PostgreSQL. |
| 5.2 | Shop creation | BLOCKED | Tenant shop route exists; API integration test not run. |
| 5.3 | Invitation/role assignment | BLOCKED | Invitation/acceptance/membership routes exist; no test execution. |
| 5.4 | Device registration | BLOCKED | Device endpoints and test exist; no test execution. |
| 5.5 | Medicine catalogue sync | BLOCKED | Bootstrap/feed and local apply code exist; test not run; multi-device behavior unproven. |
| 5.6 | Batch purchase/stock receipt | BLOCKED | Pharmacy purchase API/service and rollback test exist; no PostgreSQL run. |
| 5.7 | Sales and receipts | PARTIAL | Local sale smoke passed; UI receipt generation/printing and full cloud sale path not tested. |
| 5.8 | Cash payments | PARTIAL | Local cash checkout smoke and payment persistence code exist; amount/report reconciliation not tested. |
| 5.9 | M-Pesa sandbox or documented dependency | PASS | `backend/README.md`/`OPERATIONS.md` explicitly require Safaricom sandbox credentials, callback, and approval. No transaction was sent; this passes the documentation alternative only. |
| 5.10 | Returns/reversals preserve history | PARTIAL | Desktop `logic/sales.py::process_return` records return movements; cloud sale-return replay is unsupported and routes to review. Test local return and implement/test cloud replay. |
| 5.11 | Offline sales synchronize | BLOCKED | Client/server sync code and tests exist; tests could not run; discounted sale defect found. |
| 5.12 | Inventory reconciliation | PARTIAL | Batch movement/conflict patterns exist; full multi-terminal reconciliation and compensating movement workflow absent. |
| 5.13 | Tenant/shop reports accurate | NOT VERIFIED | Local reports exist; no report test run, and SaaS APIs expose summary rather than full report suite. |
| 5.14 | Subscription state reflected | BLOCKED | Subscription enforcement/tests exist but PostgreSQL integration unavailable. |
| 5.15 | Sensitive actions audited | PARTIAL | Audit helpers are wired to many backend/local actions; no completeness test or audit integrity test. |
| 5.16 | Cross-tenant tests pass | BLOCKED | Test exists in backend suite; pytest/dependencies blocked execution. |
| 5.17 | Cross-shop tests pass | BLOCKED | Test exists; not run. |
| 5.18 | Unauthorized object access denied | BLOCKED | Explicit scoping/auth exists and selected tests authored; route-wide IDOR tests absent/unrun. |
| 5.19 | Validation prevents unsafe DB operations | PARTIAL | Pydantic schemas, ORM and bound SQLite parameters are present; no SQL injection scanner/fuzzing. Dynamic SQL update fragments are built from allowlisted field sets in sync code. |
| 5.20 | Credentials/tokens not exposed | PARTIAL | Request logger avoids body/auth; credential hashes and tokens handled in source. No full secret/history/log/crash-report scan. |
| 5.21 | Device revocation enforced | BLOCKED | Sync auth checks `revoked_at`; test exists but not run. |
| 5.22 | Replay/duplicate protections tested | BLOCKED | Event IDs/transaction IDs and dedupe logic exist; test suite unavailable. |
| 5.23 | Admin privileges restricted | PARTIAL | Platform operator separation and metadata-only routes exist; MFA absent and no adversarial privilege test. |
| 5.24 | Critical/high known vulnerabilities resolved | FAIL | Source demonstrates unsigned callback can mark payment paid; duplicate replay implementation defect affects financial sync. Do not release until repaired and adversarially tested. |
| 5.25 | Repeatable Windows build process | PARTIAL | `build_windows.ps1`, `pharmacy_app.spec`, `version_info.txt`, `installer.iss` exist. Script/build not run and PyInstaller/Inno Setup unavailable. |
| 5.26 | Packaged application built | BLOCKED | No Windows build tool/runner; no artifact found. |
| 5.27 | Clean Windows startup | BLOCKED | No Windows VM. |
| 5.28 | Dependencies bundled/installed correctly | PARTIAL | PyInstaller spec collects selected libraries and one-file binary; no clean-machine import test or signature verification. |
| 5.29 | No production secrets bundled | PARTIAL | Source spec contains no explicit production credentials and `.env.example` placeholders only; no artifact or secret scan to prove. |
| 5.30 | Packaged DB init/migration works | BLOCKED | Windows keyring first-run path and SQLite migrations not exercised in a packaged binary. |
| 5.31 | Printing with supported printers | BLOCKED | No supported Windows printer/hardware available. |
| 5.32 | Packaged backup/restore | BLOCKED | Only source-level temp SQLite smoke; no packaged Windows test. |
| 5.33 | Install/update/rollback/uninstall documented | PASS | `DEPLOYMENT.md` documents installation, update/restore rollback and user-data retention; actual installer behavior remains untested. |
| 5.34 | Automated tests run successfully | BLOCKED | `python -m pytest -q tests` and backend equivalent both fail immediately because pytest is absent. |
| 5.35 | Integration tests against test DB | BLOCKED | No disposable PostgreSQL URL and backend deps missing; no DB was connected. |
| 5.36 | Offline recovery evidence | PARTIAL | Local outbox persistence and backup restore smoke passed; no crash, reboot, long outage or reconnection recovery evidence. |
| 5.37 | Tenant isolation evidence | BLOCKED | Auth/RLS tests exist historically/source, but not executed in this audit. |
| 5.38 | Backup restoration demonstrated | PASS | Isolated SQLite restore passed with integrity check and prior stock state recovered. Cloud and packaged restore unproven. |
| 5.39 | Cloud deployment instructions reproducible | PARTIAL | `DEPLOYMENT.md`, backend setup docs give config/migration/TLS steps; no container/orchestrator, lock, real deployment or reproducibility run. |
| 5.40 | Monitoring/health checks exist | PARTIAL | `/health/live`, `/health/ready`, structured request logging exist; external error monitoring, alerts and operational dashboards are absent. |
| 5.41 | TLS/secure configuration documented | PASS | Backend README/deployment docs describe TLS, Redis, secrets, DB roles; runtime TLS depends on external reverse proxy and was not deployed. |
| 5.42 | Regulatory/operational requirements listed | PASS | Operations docs list Safaricom, Kenyan privacy/pharmacy review, backups and MFA gates. This is documentation, not regulatory approval. |
| 5.43 | Release version and Git commit recorded | FAIL | Build metadata says 1.0.0 and current HEAD is `defafaf`, but no release record binds a built artifact to a clean committed tree; working tree has broad modifications/untracked implementation. Create tagged, clean release candidate and artifact hashes. |

## D. Critical blockers

### P0 — Do not release

1. **Forged Daraja callback can grant subscription time.** `backend/app/api/v1/payments.py` exposes a public callback; `backend/app/services/daraja.py` explicitly notes callbacks have no cryptographic signature, then marks a payment `paid` based on checkout ID plus caller-supplied amount, phone and receipt. Those fields are not provider proof. Require an independently authenticated Safaricom transaction verification/reconciliation before granting entitlement, and add a test submitting forged callbacks.
2. **Offline replay implementation is shadowed.** `backend/app/services/pharmacy.py` defines `record_replayed_sale` twice. The second function (the one Python calls) omits discount/tax calculation and stable sale-item IDs handled by the first. Discounted offline sales can conflict or reconcile with incorrect totals. Remove duplication and test discounted/taxed replay, customer credit, line IDs, repeated event and rollback.

### P1 — Major missing or unreliable behavior

1. **No proven full end-to-end/integration run.** Both pytest commands are blocked; no PostgreSQL migration, app startup, multi-shop/device path, crash/outage/reconnect, Windows package, printer, or Daraja sandbox test was completed. Run an isolated CI matrix before pilot.
2. **No cloud return workflow.** Local returns exist, but sync event is not applied by cloud API and will need review. Implement or explicitly design reconciliation before representing cross-device stock/financial history as complete.
3. **Desktop migration/restore safety is only partially proven.** Legacy inline migrations commit incrementally. Local backup smoke passed, but failure-injection and packaged restore tests are absent.
4. **No clean Windows build evidence.** The build script/spec are source only; no signed artifact, clean install/upgrade test, keyring test, printer test or uninstaller test.
5. **No external security/dependency scan or MFA.** Current controls have no independent penetration assessment; platform operators are not protected by MFA in source.

### P2 — Important incomplete items

1. **SaaS surface is API-only.** No graphical platform/tenant SaaS administration console. Current desktop UI remains a local pharmacy app with online sign-in/sync.
2. **Daraja status model and reconciliation are incomplete.** Retail M-Pesa sales are not integrated; subscriptions only. STK query does not independently confirm success, and automated polling/reconciliation jobs are absent. Renewal duration is hard-coded to 30 days.
3. **Operational monitoring and recovery are not deployed.** Health endpoints exist, but no alerting/error-monitoring service or cloud PITR restore evidence.
4. **Shop coordination is not multi-host tested.** LAN service uses HTTP/HMAC and expects a trusted private LAN; no physical multi-terminal, server-loss or prolonged-outage exercise.

### P3 — Documentation/release hygiene

1. Working tree is dirty with Stage 4/5 code not committed; no signed/tagged release artifact or artifact-to-commit record.
2. Dependency ranges are not fully locked; no successful clean-room install/build evidence.
3. Test documentation includes historical results; attach CI logs and distinguish earlier runs from the current audited tree.

## E. Test evidence

### Commands executed

| Command/check | Result |
|---|---|
| `git rev-parse --show-toplevel`, `git status --short`, `git log -8 --oneline --decorate` | Passed as read-only inspection; HEAD `defafaf`; broad modified/untracked Stage 4/5 tree. |
| `python --version` | Python 3.13.7. |
| `python -m pytest -q tests` | Blocked: `No module named pytest`. |
| `python -m pytest -q backend/tests` | Blocked: `No module named pytest`; backend `conftest.py` also imports missing `pydantic`. |
| `python -m compileall -q main.py database logic ui backend/app backend/tests` | Passed; syntax only. |
| `git diff --check` | Passed. |
| Tool checks for `pyinstaller`, `iscc`, PostgreSQL commands | PyInstaller/Inno Setup compiler unavailable. `psql`/`pg_isready` binaries exist, but no database connection was attempted. |
| Isolated temporary SQLite smoke | Passed local cash checkout, stock deduction, persisted sale/outbox after connection reopen, backup, restore, and SQLite integrity check. Temp directory was removed automatically. |

### Not performed / blocked

- No desktop pytest test passed in this audit; no test failed beyond collection/startup because pytest is missing.
- No backend pytest test passed or failed; test collection was blocked before test execution.
- No integration database test/migration was run; no test DB was supplied and no database was contacted.
- No live/sandbox M-Pesa request or callback was sent.
- No full UI, receipt printer, Windows build, clean Windows startup, install/update/uninstall or Credential Manager test.
- No process crash/reboot, prolonged disconnection, clock skew, multi-host LAN, cloud outage recovery, restore drill, SQL injection scan, secret-history scan, dependency scan or penetration test.
- No Kenyan legal/regulatory verification; source documentation lists this as a deployment prerequisite.

## F. Architecture findings

- **Tenant isolation:** Reasonable defense-in-depth source design: explicit membership/permission/shop checks, composite foreign keys and forced PostgreSQL RLS. Not proven against a running non-superuser PostgreSQL role in this audit. Platform operator RLS scopes require dedicated adversarial tests.
- **Tenant/shop/device relationships:** Models and device HMAC authentication bind devices to tenant/shop; source structure is coherent. Runtime authorization and revocation remain unverified.
- **Batch inventory:** Separate medicine/batch models, FEFO-like local batch selection, expiry rejection, movement records and sale price/cost snapshots exist. A direct local sale smoke passed. Cloud/offline replay has the duplicate-function defect and cloud returns are missing.
- **Offline synchronization:** Durable SQLite outbox, stable IDs, retry state, change feed/checkpoint and conflict UI exist. Smoke confirms a sale and outbox event persist together; timeout, crash, restart and long-outage guarantees were not demonstrated.
- **Multi-computer coordination:** SQLite coordinator provides serialized service methods and signed requests. It is not a proven LAN deployment; docs require common baseline, trusted network, and reconciliation after coordinator failure. No shared stock guarantee exists during terminal partition.
- **Payment reconciliation:** Daraja HTTP adapter and subscription payment ledger exist, separate from retail payments. Public unsigned callback can mark paid, which is not safe provider reconciliation. Retail M-Pesa tender is absent.
- **Local/cloud boundary:** SQLite remains the desktop local store; PostgreSQL is backend central store; keys are intended for OS keyring; platform support defaults to metadata. Local DB/backups are not application-encrypted, and local admin/filesystem ownership can bypass UI controls.

## G. Recommended remediation plan

| Priority | Affected files/modules | Expected behavior | Required confirmation test |
|---|---|---|---|
| P0 | `backend/app/services/daraja.py`, `backend/app/api/v1/payments.py`, `backend/tests/test_api_integration.py` | Never grant subscription from untrusted callback claims; provider-verifiable reconciliation is required; preserve pending/unknown state on ambiguity. | Forged callback, mismatched callback, duplicate callback, genuine sandbox payment and status-query tests; no success before provider confirmation. |
| P0 | `backend/app/services/pharmacy.py`, `backend/app/api/v1/sync.py`, `backend/tests/test_api_integration.py` | Exactly one correct replay implementation; discount/tax, stable item IDs, money snapshots and credit balance reconcile atomically. | Online/offline parity cases, discounts, taxes, partial payment, returns, duplicate IDs and rollback under PostgreSQL. |
| P1 | `backend/tests`, `tests`, CI config | Reproducible dependency install and all suites executed against temporary SQLite and disposable PostgreSQL. | CI report with clean migrations, tenant/shop isolation, concurrency, sync replay and subscription tests. |
| P1 | `database/db_manager.py`, `logic/backup.py` | Versioned, recoverable desktop migrations; restore never loses valid committed data and protects sidecars. | Legacy fixture upgrades, interrupted migration, corrupt backup, WAL restore, rollback and packaged app recovery. |
| P1 | `logic/sync.py`, `network/shop_server.py`, `backend/app/api/v1/sync.py` | Explicit event coverage and safe cloud returns/reversals; conflicts remain visible and financial records append-only. | Crash/timeout/restart/failover tests across two physical or virtual terminals and cloud. |
| P1 | `build_windows.ps1`, `pharmacy_app.spec`, `installer.iss`, `main.py` | Build, sign and run versioned application on clean Windows 10/11 x64 without secrets. | Clean VM install/upgrade/uninstall, keyring/bootstrap, DB migration, printing, offline sale and restore. |
| P1 | API security, auth and deployment | MFA for platform operators, full rate-limit checks, independent threat/penetration review, secret and dependency scans. | Adversarial report; no unresolved critical/high finding; rerun tenant/RLS tests using runtime role. |
| P2 | SaaS admin UI/report APIs | Provide intended operator/tenant administration UX and complete scoped reports, or explicitly scope API-only launch. | Role-specific UI/API tests across assigned shops and tenants. |
| P2 | `backend/OPERATIONS.md`, deployment environment | Automated reconciliation/expiry jobs, monitoring, alerts, backup/PITR and restore/runbook ownership. | Demonstrated incident, payment reconciliation and isolated PITR restore exercises with recorded RPO/RTO. |
| P2 | Compliance/deployment owners | Obtain Safaricom approval and Kenyan privacy/pharmacy review for actual deployment and data flows. | Written approvals, DPIA/registration/legal review as applicable, reviewed medicine schedule and SOPs. |

No remediation was implemented during this audit. The only new repository artifact is this requested report.

## H. Release recommendation

**INTERNAL TESTING ONLY.** Do not release to a controlled pilot or production based on this evidence. The callback can grant subscription entitlement without provider authentication, offline sale replay has a shadowed incorrect implementation, automated suites did not run, and Windows/cloud recovery has not been demonstrated. Resolve both P0s, run the complete isolated test/security matrix, then require Safaricom and Kenyan regulatory/operational approvals before reconsidering release.

## I. Final declaration

### Proven operational in this audit

- Python source compiles and `git diff --check` passes.
- A local cash sale changed stock and persisted its outbox event in an isolated SQLite database.
- A SQLite online backup restored the prior state and passed `PRAGMA integrity_check` in an isolated temporary database.

### Code exists but has not been fully tested

- FastAPI/PostgreSQL tenant, membership, shop, device, inventory, subscription, admin and sync APIs.
- Alembic/RLS policies, authentication/session management, rate limits, audit services and device authorization.
- Desktop login, pharmacy screens, local returns/reports/printing, cloud login, sync client, LAN coordinator and UI status/conflict screens.
- Daraja OAuth/STK/status adapter and subscription ledger.
- Windows PyInstaller/Inno Setup configuration and build script.

### Missing or demonstrably defective

- Cloud return/reversal replay is not implemented.
- Retail-sale M-Pesa workflow is not implemented; current Daraja integration is subscription billing.
- Duplicate `record_replayed_sale` definition means the later, incomplete implementation is used.
- Unsigned callback data can mark a subscription payment successful without independent Safaricom verification.
- No graphical SaaS administration console, platform MFA, external monitoring integration, or automated reconciliation service was found.

### Blocked pending environment/evidence

- Pytest suites, PostgreSQL migrations/integration tests, actual backend startup, and RLS adversarial tests.
- Daraja sandbox transaction, clean Windows package and VM, printing, multi-host coordinator, prolonged offline/crash/restart tests, cloud restore drill, dependency/secret scanning and Kenyan regulatory review.

**Final status: partially converted SaaS; internal testing only. Not production-ready and not yet pilot-ready.**
