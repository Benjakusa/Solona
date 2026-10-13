# SaaS operations and production readiness

## Availability and subscription policy

A subscription expiry changes cloud authorization for plan-protected writes, shop/device provisioning, and billing changes after any configured grace period. Authentication, tenant/shop reads the membership permits, audit/history, billing status, local SQLite operations, and authorized data export remain available. Expiry never deletes tenant data or disables viewing/exporting local business records. Offline authorization follows the desktop's bounded 72-hour cached-membership policy; local sale acceptance and sync reconciliation remain governed by its offline rules. Do not extend the offline window without a security and privacy review.

## Payment operations

Use Daraja sandbox keys and a Safaricom test shortcode to exercise the callback path. Keep credentials in a secret manager or environment injection. A reachable callback endpoint and TLS are required. STK initiation and status-query acceptance are pending states. Callback success is accepted only with matching checkout/merchant references, whole-shilling amount, phone and unique receipt; mismatches, late/conflicting callbacks and ambiguous network timeouts require provider reconciliation. Callback payloads do not have a Safaricom-verifiable signature in this integration; enforce TLS, rate limits, and reconciliation against the Daraja portal/provider records. Never manually set `paid` from customer evidence alone.

## Backups and recovery

Use encrypted PostgreSQL base backups plus WAL archiving/PITR, with keys held separately. Define RPO/RTO with the operator. Restore into an isolated PostgreSQL instance at least quarterly and after material schema/backup changes; verify Alembic head, row counts, tenant/shop separation, sample tenant exports, sale/payment totals, and application readiness before cutover. Tenant JSON exports are portability aids and are not database backups. Back up desktop and shop-coordinator SQLite using the SQLite online backup API; test restore and reconcile unsynced outboxes before resuming multi-device sales. Keep an incident runbook with provider, hosting, database, and Safaricom escalation contacts.

## Logging, monitoring, and deployment

Application logs are structured and exclude credentials and request bodies. Production still requires an external error-monitoring service, alert routing, retention policy, and tested redaction settings; no third-party monitoring account is configured by this repository. Deploy behind a trusted TLS reverse proxy, restrict forwarded-header trust, use PostgreSQL TLS, a least-privilege non-superuser runtime role with forced RLS, and shared Redis rate-limit storage. `/health/live` is process liveness; `/health/ready` checks PostgreSQL. Roll out migrations from a separate deployment identity and test them against a restored production-like copy. Revoke compromised device credentials and user sessions from the operator API; investigate outstanding sync conflicts before restoring service.

The current operator and tenant administration surfaces are authenticated API endpoints, not a finished graphical administration console. Configure an authorized operator client and tenant client with the documented API scopes before piloting; do not expose platform routes to tenant users. Automated expiry jobs, automated Daraja reconciliation polling, external error monitoring, and MFA need deployment-owned services or follow-on implementation.

## Kenya approvals and compliance prerequisites

Before production, obtain Safaricom Daraja app/product enablement, production consumer credentials, shortcode/till/paybill and transaction-type approval, and a public HTTPS callback URL. Confirm callback and status-query behavior with Safaricom's current Daraja documentation and sandbox/production onboarding; callback source authenticity is not cryptographically proven by this implementation.

Have Kenyan counsel/privacy personnel assess the Data Protection Act 2019 and 2021 regulations, ODPC controller/processor registration and DPIA requirements, health-data handling, lawful basis, patient notices/rights, retention/deletion, breach notification, processor contracts, cross-border hosting, and safeguards. Confirm pharmacy premises/practitioner obligations, PPB classification and prescription/controlled-medicine records with a Kenyan pharmacy compliance professional. The generic medicine `is_controlled` flag is not a legal schedule. Complete a threat model, penetration test, restore drill, incident exercise, and operator MFA/strong identity deployment before handling production patient or payment data. These are explicit deployment gates; code changes alone cannot grant regulator or Safaricom approval.
