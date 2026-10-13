# Production readiness

**Status: not ready for production pharmacy operations.** This file records release gates that remain after the current hardening changes. A gate should be closed only with reproducible evidence and a named owner/reviewer.

## Security and authorization

- Enforce authorization in desktop logic functions, not only in UI visibility.
- Complete role checks for returns, discounts, payments, stock adjustments, and account administration.
- Add login throttling and review password-hash work factor and migration behavior.
- Add TLS with certificate verification/pinning for LAN traffic and protect against request replay.
- Validate every sync event schema and define safe behavior for poison events and conflicts.
- Review credential/key lifecycle, key rotation, and secret backup/restore.

## Data integrity and pharmacy workflows

- Finish financial reconciliation coverage for reports, cash drawer, all payment types, refunds, and partial returns.
- Complete expiry, stock threshold, barcode uniqueness, legacy-null expiry, and stock intake validation.
- Verify all sync event types preserve identifiers, timestamps, costs, payment details, and audit history.
- Define and test multi-terminal stock reconciliation and coordinator recovery.

## Operations and recovery

- Encrypt backups, validate restore paths and compatibility, and conduct documented restore drills.
- Finish database migration runner behavior and test upgrades from supported versions.
- Add structured operational logging, monitoring, health checks, bounded server concurrency, and incident/recovery procedures.
- Document and test data retention, privacy, access review, and Kenyan pharmacy/payment compliance obligations with qualified reviewers.

## Platform and release

- Replace platform-specific startup locking and verify first-run, keyring, printing, backup, upgrade, and uninstall on clean Windows machines.
- Verify Linux/macOS behavior if they are supported targets.
- Add reproducible CI, dependency/security review, release signing, and an end-to-end regression matrix.
- Run the complete desktop and PostgreSQL test suites against clean disposable environments after the final code changes.

See [AUDIT_RESULTS.md](AUDIT_RESULTS.md) for completed changes and current test evidence. See [DEPLOYMENT.md](DEPLOYMENT.md) and [BACKUP_AND_RECOVERY.md](BACKUP_AND_RECOVERY.md) for existing operational notes.
